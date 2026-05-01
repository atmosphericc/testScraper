# Walmart Profile
## Last Researched: 2026-04-30
## Last Updated: 2026-04-30 (full research pass — behavioral baselines, HUMAN Security update, detection audit consolidation)

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
4. Order Confirmation    https://www.walmart.com/checkout/thankyou?version=v3/
                         OR /checkout/order-confirmation
                         OR /order-confirmation (URL varies by A/B test cohort)
```

Walmart's checkout is a **React SPA**. The URL stays at `/checkout` throughout steps 3a–3d; only the in-page "step" component changes. Rely on DOM presence of step-specific elements, not URL, to know where you are.

### Add to Cart

**Priority order in live `purchase_executor.py` `ATC_SELECTORS` (last verified: 2026-04-30):**

1. `button[data-automation-id="atc"]` — **PRIMARY** (confirmed live selector; "add-to-cart-btn" is wrong/stale)
2. `button[data-automation-id="add-to-cart-btn"]` — legacy fallback (was primary pre-2026-04-10; now secondary)
3. `button[data-dca-event="addToCart"]` — DCA event marker fallback
4. `button[data-tl-id="ProductPrimaryCTA-cta_add_to_cart_button"]` — analytics ID fallback
5. `button[data-dca-name="ItemBuyBoxAddToCartButton"]` — DCA name fallback
6. `button:has-text("Add to cart")`, `button:has-text("Add to Cart")` — text fallbacks
7. `button:has-text("Pre-order")`, `button:has-text("Pre-Order")`, `button:has-text("Preorder")` — preorder variants
8. JS text-content fallback: `button.textContent.includes('add') && button.textContent.includes('cart')`

> **CRITICAL NOTE**: The profile previously listed `add-to-cart-btn` as primary. This was identified as a root-cause ATC failure in 2026-04-10 (MEMORY.md ATC click fix). Walmart's live DOM uses `data-automation-id="atc"`. The `add-to-cart-btn` value may appear in some A/B cohorts but is no longer reliable as the primary selector.

**Buybox lazy-load behavior**: The buybox React island (which contains the ATC button) is **not present in SSR HTML**. Walmart intentionally strips it. `window.__NEXT_DATA__` populates at ~0.3s but `data-automation-id` attributes are only attached when React's reconciler commits the buybox island — typically 3-7s post navigation on a cold tab. The `_wait_for_page_ready()` method handles this.

**ATC confirmation signals (post-click — any one sufficient):**
- Button text changes to "Added" or button becomes disabled — checked via `btn.textContent.includes('added') || btn.disabled`
- `[data-automation-id="cart-flyout"]`
- `[data-automation-id="atc-flyout"]`

**ATC confirmation approach (current)**: After clicking, a single fast check runs at 0.3s post-click for flyout/button-state-change. The flow **proceeds to cart regardless** of whether confirmation is detected — cart verification is the authoritative check for ATC success. Do not wait more than ~1s for ATC confirmation; the 12s confirmation window from earlier implementations was reverted (2026-04-10) because it created unnecessary latency on silent-ATC sessions.

### Human Timing Baseline — Add to Cart

Based on usability research and behavioral biometric baselines:

| Action | Human Baseline | Bot Risk if Outside Range |
|--------|---------------|--------------------------|
| Page load → first scroll | 1.5–4s | < 0.5s = immediate bot flag |
| First scroll → ATC click | 2–8s (reading product info) | < 1s = bot flag |
| ATC click → cart navigation | 0.5–2s natural pause | < 200ms = PerimeterX flag |
| Total product page dwell | 5–20s | < 3s = high bot score |
| Cart page dwell before checkout | 3–10s | < 1.5s = Akamai bot flag |
| CVV entry per digit | 80–200ms inter-key delay | < 50ms = PerimeterX keystroke flag |
| CVV full entry (3 digits) | 400–800ms total | < 150ms total = flagged |
| Place Order click timing | 2–5s after review page load | < 1s = bot signal |
| Total checkout (cart → confirmation) | 45–120s | < 20s = high suspicion |

> These are synthesized from usability study data (38 of 40 participants completed checkout in < 100s) and behavioral biometric research. The 45–120s checkout window represents the realistic human range. Bot sessions completing checkout in < 20s are detectable by both Akamai and PerimeterX session-level models.

### Cart Page

- Cart URL: `https://www.walmart.com/cart`
- Cart item containers (priority order):
  - `[data-automation-id="cart-item"]` — primary
  - `[data-testid="cart-item"]` — A/B variant
  - `[data-automation-id="cart-item-container"]` — wrapper variant
  - `[data-testid="cart-item-container"]` — wrapper variant (testid)
  - `button[data-automation-id="remove-item"]` — proxy: only present when item exists
  - `input[data-automation-id="item-qty"]` — proxy: quantity spinner only present when item in cart
  - `button[aria-label*="Remove"]` — proxy: stable across deploys
- Secondary verification: `window.__NEXT_DATA__.props.pageProps.initialData.data.cart.cartLines` (or `.lineItems`/`.items`) — JS-state probe used when CSS selectors fail; returns item count directly from React store
- Empty cart signal: body text contains "your cart is empty"
- Checkout button (on cart page):
  - `button[data-automation-id="checkout-btn"]`
  - `a[data-automation-id="checkout-btn"]`
  - `button[data-automation-id="continue-to-checkout"]`
  - `a[data-automation-id="continue-to-checkout"]`
  - `button:has-text("Continue to checkout")`, `button:has-text("Checkout")`

**Delivery selection on cart page**: Before clicking Checkout, the bot calls `_select_delivery_on_cart()` which scans for `button`, `[role="tab"]`, `[role="radio"]`, `[role="option"]`, `label` elements whose text matches `/^(Delivery|Ship(ping)?)$/i` or contains "delivery" (< 40 chars, not "free delivery"). This sets the fulfillment mode on the cart page; the choice persists into checkout. This is separate from `_select_delivery_option()` which runs inside the checkout step loop.

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
- Body text contains "payment", "shipping", "delivery", "order summary", "place order", "credit", "fulfillment", or "checkout"

Address fields (when address entry required — rare for saved accounts):
- Street: `input[name="addressLineOne"]`, `input[autocomplete="address-line1"]`
- City: `input[name="city"]`, `input[autocomplete="address-level2"]`
- State: `select[name="state"]`, `select[autocomplete="address-level1"]`
- ZIP: `input[name="postalCode"]`, `input[autocomplete="postal-code"]`

Payment / CVV:
- CVV input: `input[name="cvv"]`, `input[autocomplete="cc-csc"]`, `input[placeholder*="CVV"]`, `input[placeholder*="CVC"]`, `input[aria-label*="CVV"]`, `input[aria-label*="security code"]`
- CVV is nearly always required even with saved cards on Walmart
- CVV is typed character-by-character with randomized inter-key delays (80-150ms per char) via CDP `dispatchKeyEvent` — `set_value()` atomic write is detectable by PerimeterX keystroke monitoring
- **keyDown hold duration**: 50–150ms per key (raised from 10–30ms in Patch 4, 2026-04-25). Sub-50ms hold is below physical human minimum and is flagged by HUMAN Security's keystroke biometric model.

### Place Order Button

```
button[data-automation-id="place-order-btn"]
button:has-text("Place order")
button:has-text("Place Order")
button:has-text("Submit order")
```

### Confirmation Page

- Confirmed current URL pattern: `https://www.walmart.com/checkout/thankyou?version=v3/`
- Regex fallback: `order-confirmation|order/confirm|thank-you|order-placed`
- Order number selector: `[data-automation-id="order-confirmation-number"]`, `[data-automation-id="confirmation-order-id"]`
- Text fallbacks: `h1:has-text("Your order is confirmed")`, `h1:has-text("Thank you")`, `span:has-text("Order #")`
- Order ID is numeric, typically 13–16 digits. Extract with `\d{6,}` from element text or URL.
- Order detail URL: `https://www.walmart.com/orders/<order_id>`

### Delivery Day Modal (added 2026-04-29)

A delivery day selection modal appears **mid-checkout after "Deliver here" is clicked** on the address step — NOT only at flow end. This is a known failure point.

**Detection signals:**
- `[data-automation-id*="delivery-day"]`
- `[data-automation-id*="delivery-window"]`
- `[data-automation-id*="select-delivery"]`
- Text: "Today", "Tomorrow", "Next Day", "Standard"
- `button[role="radio"][aria-label*="deliver"]`
- `input[type="radio"]` with aria-label
- `[role="dialog"] button:not([aria-label*="close"])` — generic fallback

**Current handling**: `_handle_delivery_day_modal()` is called on every iteration of the `_confirm_shipping()` loop (fixed 2026-04-29). Returns `True`/`False`. After dismiss, wait 500–1000ms for DOM to settle. Also called proactively after `_select_delivery_option()`.

### React Hydration and Page Readiness

Walmart product pages use Next.js + React. The buybox (containing the ATC button) is loaded client-side only — not in SSR HTML. The `_wait_for_page_ready()` method polls until:
1. `window.__NEXT_DATA__` is truthy (React initialization signal)
2. At least one ATC selector is visible (`display !== 'none'` and `visibility !== 'hidden'` and `opacity > 0`)

**Timeouts**:
- Cold navigation (new URL): `timeout=13000` — covers the 7-10s buybox lazy-load window on cold Tab 2
- Already on product page (Tab 2 pre-warmed): `timeout=2000` — button should be immediately available

**Implementation**: `_PAGE_READY_JS` polls all CSS-attribute ATC selectors first, then falls back to `querySelectorAll('button')` text-content search for "add" + "cart". This single JS blob executes in one browser round-trip per 150ms poll interval. Logs `Page ready in X.Xs — ATC button found via: <selector>` on success.

**Expected timing**: On a warmed Tab 2 (already on product page), button found in < 0.5s. On cold navigation, typically 2-4s when text-content renders before `data-automation-id` is attached, up to 7-10s for full React commit of buybox island.

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
- **Recommendation: use account login flow.** Saved address + saved card with CVV-only entry is faster and scores lower.

### Walmart+ Account Differences

- Walmart+ members get early access to limited-release product drops (items listed as "Walmart+ members only" before general drop)
- Walmart+ early access drops: product page shows "Early Access" banner; ATC button locked until early access window opens (typically 1 hour before public)
- Must be logged into Walmart+ account to access these

### Interstitial Pages / Prompts

1. **Virtual Queue** (`/blocked` or queue iframe): Appears on high-demand drops. See `queue_handler.py` for detection/handling.
2. **Fulfillment Selector**: When item supports both Ship and Pickup, a modal asks you to choose. Always click Delivery/Ship first — handled on cart page by `_select_delivery_on_cart()` and in checkout loop by `_select_delivery_option()`.
3. **Delivery Day Modal**: Appears after "Deliver here" click during address step. Handled by `_handle_delivery_day_modal()` in `_confirm_shipping()` loop (fixed 2026-04-29).
4. **Substitution Prompt**: Appears during grocery/pickup checkout when item is OOS. Asks if you want substitution.
5. **Age Verification**: Appears for alcohol, certain medications. Rare for typical product types.
6. **"Robot or Human?" CAPTCHA** (`/blocked`): "Activate and hold the button to confirm you're human." This is PerimeterX (HUMAN Security) challenge, not traditional CAPTCHA. See Anti-Bot section.
7. **Store Pickup Prompt**: If detected user location has Walmart store, a modal may prompt "Pick up at [store]?" for eligible items. Must dismiss or select Ship.

### Fulfillment: Store Pickup vs. Ship-to-Home

- Ship-to-home flow: standard sequence above
- Store pickup flow: checkout adds fulfillment step; address step replaced by store selection. Payment and review steps remain.
- Automation should always select ship-to-home (Delivery) to avoid store-specific inventory gaps and skip store selection step.

### Express Checkout / Walmart Pay

- Walmart Pay (in-store QR code only) does not apply to web checkout
- No "Express Checkout" button for third-party wallets on main checkout flow
- Apple Pay / Google Pay listed as supported but appear as options within standard checkout payment step
- "Buy Now" / direct-to-checkout buttons exist on some product pages (especially mobile); navigate directly to `/checkout` skipping cart. Selector: `button:has-text("Buy now")`, `button[data-automation-id="buy-now-btn"]`

---

## Anti-Bot Stack (Akamai + PerimeterX/HUMAN + Cloudflare)

### Overview

Walmart runs a **layered, three-vendor anti-bot stack** — one of the most aggressive in US retail:

| Layer | Vendor | Role |
|-------|--------|------|
| 1 | **Akamai Bot Manager** (v2/v3) | Edge WAF, TLS fingerprinting, JavaScript challenge, `_abck` cookie |
| 2 | **PerimeterX / HUMAN Security** | Behavioral analysis, `_px3` cookie, interactive "hold button" challenge |
| 3 | **Cloudflare** | CDN, DDoS mitigation, additional IP reputation layer |

**Difficulty rating: 9/10.** Both Akamai and PerimeterX independently challenge, so bypass of one doesn't guarantee passing the other.

### Akamai Bot Manager — Detection Vectors

#### 1. TLS Fingerprinting (JA3/JA4) — Highest Signal
- Each TLS client handshake produces a JA3 hash (cipher suites, TLS version, extensions, ALPN)
- Python `requests`, `httpx`, `scrapy` have well-known bot JA3 hashes and are blocked immediately
- Chromium-based browsers (real Chrome, patchright) produce legitimate JA3 hashes
- **Critical**: TLS fingerprint must match known good browser signature. `curl_cffi` with `impersonate="chrome120"` needed for raw HTTP approaches.
- Akamai also reads **HTTP/2 settings frames, window updates, and frame ordering** — not just TLS. A client that does not behave like a real browser at the HTTP/2 layer is flagged before the first request completes.

#### 2. IP Reputation
- Datacenter IPs (AWS, GCP, Azure, DigitalOcean, etc.) classified negatively and typically blocked at edge
- Residential and mobile IPs score positively
- **Walmart specifically enforces US-only proxies**; non-US IPs (including Canada, Mexico) trigger 456 blocks or instant challenges

#### 3. JavaScript / Browser Fingerprinting
Akamai's client-side script collects 100+ signals including:
- `navigator.webdriver` — must be `undefined` (not `false`)
- `navigator.plugins` — must have 3+ real plugin objects (automation contexts return 0)
- `navigator.mimeTypes` — must be populated
- `navigator.languages` — must be `["en-US", "en"]` or similar
- `window.chrome` — must exist with `runtime`, `loadTimes`, `csi`, `app` properties (including `webstore`, `cast`, `bluetooth` sub-properties — see Issue 10 in audit)
- Canvas fingerprint (pixel rendering via WebGL/2D canvas)
- Audio context fingerprint
- Screen resolution and color depth
- Hardware concurrency (`navigator.hardwareConcurrency`)
- Device memory (`navigator.deviceMemory`)
- **Timing traps**: Akamai measures execution time of specific JS code to detect debuggers or non-native execution environments

#### 4. `_abck` Cookie and `sensor_data` Payload

The `_abck` cookie is the primary Akamai session token:
- Set on first page load via JavaScript challenge (`/akam/11/pixel_xxxxx` or similar endpoint)
- Contains an **encrypted, base64-encoded telemetry blob** validated on every subsequent request
- Structure: two-phase encryption — (1) colon-delimited JSON payload shuffled by a PRNG seeded with JS file hash, then (2) character substitution using `bm_sz` cookie-derived hash
- Each `sensor_data` POST is unique per session (replay attack prevention via session-specific seeds)
- Initial sensor requests use default cookie hash of `"8888888"`; subsequent requests derive hash from returned `bm_sz` cookie value
- `_abck` validated server-side on every request; invalid or missing cookie results in 403 "Pardon Our Interruption" page
- The sensor payload includes **request timestamp sequences** — machine-regular timing patterns (e.g., exactly 1.0s intervals) are high-confidence bot indicators in the payload

**Full Akamai cookie set on Walmart:**

| Cookie | Purpose | HTTP-only |
|--------|---------|-----------|
| `_abck` | Bot Manager session token (encrypted telemetry) | No |
| `ak_bmsc` | Akamai cache/security optimization, distinguishes humans from bots | Yes |
| `bm_sz` | Used to seed `_abck` encryption in subsequent requests | No |
| `bm_sv` | Akamai cache function | No |

#### 5. HTTP Protocol / Header Analysis
- Akamai checks for HTTP/2 (modern browsers use HTTP/2; many scraping libraries default to HTTP/1.1)
- Header order matters — Chrome sends headers in specific order; reordered headers are a bot signal
- `Origin`, `Referer`, `User-Agent`, and `Accept-Language` must be present and consistent
- Missing or incorrect `Sec-Fetch-*` headers (e.g., `Sec-Fetch-Site`, `Sec-Fetch-Mode`) are a signal

#### 6. Behavioral / Request Pattern Analysis
- Mouse movement patterns: natural Bezier curves vs. straight lines; PerimeterX/Akamai both flag zero intermediate mouse positions before clicks
- Click coordinates: real users click slightly off-center; exact-center repeated clicks are a bot signal
- Scroll behavior and timing: humans scroll with variable speed and momentum
- Time-on-page before interaction (see Human Timing Baseline table above)
- Navigation sequence: Akamai expects product page → cart → checkout, not direct checkout URL
- **Request rate regularity**: machine-clock request intervals (e.g., exactly 1.0s every time, deterministic stagger) are among Akamai's highest-confidence bot indicators (see Detection Audit 2026-04-30, Issue 1)
- **Navigation history sequence**: visiting the same warmup sites in the same order every session is captured in the `_abck` sensor payload (Issue 3 in audit, fixed 2026-04-30)

### PerimeterX / HUMAN Security — Detection Vectors

As of 2026, HUMAN Security (formerly PerimeterX) analyzes **2,500+ signals per interaction** and uses 400+ ML algorithms per customer:

- Deployed on checkout-critical pages (cart, `/checkout`, payment step)
- Primary token: `_px3` cookie — clearance token with a ~60 second TTL on high-security pages
- Also uses `_pxvid`, `pxcts`, `_pxhd` cookies
- Per-customer ML models trained on that site's specific historical traffic — detection rates improve over time as the model learns the bot's behavioral fingerprint

**Key behavioral signals monitored:**
- Mouse acceleration curves (natural deceleration near targets vs. linear velocity)
- Click coordinates and precision (humans click slightly off-center, bots hit exact center)
- Inter-event timing distributions (humans show wide variance; bots show tight clusters)
- Keystroke dynamics: keyDown hold duration (< 50ms is below human physical minimum), inter-key timing, key sequence patterns
- Scroll momentum and patterns
- Touch gestures (mobile): pressure, speed, angle
- Device motion (hand tremors, movement patterns)
- Cognitive load signals: pauses that suggest reading vs. instant action
- Session duration and page dwell time
- Time of day and location consistency

**The press-and-hold challenge specifically**: It is not just about holding the button. The collector script emits a signed telemetry payload before, during, and after the interaction. HUMAN validates:
- TLS fingerprint consistency with `_px3`/`_pxvid`/`_pxhd` cookie triple plus `X-PX-Authorization` header
- Hold duration (minimum ~6 seconds; HUMAN rejects durations inconsistent with human reaction time distribution)
- Mouse trajectory during hold (must show micro-jitter, not perfectly stationary)
- The full signed behavioral payload — not just the button interaction in isolation

### Known Detection Triggers (Walmart-Specific)

1. Direct navigation to `/checkout` without prior cart session — immediate block
2. Cart → checkout transition faster than ~1.5 seconds — high bot score
3. Clicking ATC button at exact center coordinates without prior scroll or mouse movement path
4. Missing `Referer` header on cart page load (should be product page URL)
5. Reusing `_abck` cookies across different IP addresses
6. Non-US proxy IP at any point in session (456 block)
7. `navigator.webdriver === true` (not patched)
8. Zero browser plugins
9. Rapid-fire product page loads without human-like pauses
10. Session with no prior browsing history (cold sessions score lower)
11. **Machine-regular stock check intervals** — deterministic 1.0s/333ms/666ms stagger pattern in sensor payload (Issue 1, audit 2026-04-30) — FIXED
12. **Fixed modal dismiss coordinates** — same `(512, 400)` mouse_move target every run (Issue 2, audit 2026-04-30) — FIXED
13. **Identical warmup site visit sequence** — Google→Amazon→Reddit→YouTube→eBay fixed order every session (Issue 3, audit 2026-04-30) — FIXED
14. **Zero mouse movement before clicks** — no intermediate positions before ATC, CVV, or Place Order clicks (Issue 8, audit 2026-04-30) — FIXED
15. **Missing `bm_sz` cookie** — causes sensor.js to use default seed `8888888`, a known bot indicator (Issue 9, audit 2026-04-30) — FIXED
16. **Uniform modal pause timing** — fixed pause durations after modal dismissal (Issue 4, audit 2026-04-30) — FIXED
17. **Fixed ATC retry delays** — uniform 300ms between all retries (Issue 5, audit 2026-04-30) — FIXED
18. **Warmup pages with zero interaction** — browsing without scrolling/mouse movement (Issue 7, audit 2026-04-30) — FIXED

### PerimeterX Cookie Family

| Cookie | TTL | Role |
|--------|-----|------|
| `_px3` | ~60s on checkout, session on browse | Primary clearance token — HMAC-SHA256 signed verdict |
| `_pxvid` | Session | Persistent visitor identity — cold visitor = higher initial score |
| `_pxhd` | Session | Encrypted device fingerprint |
| `pxcts` | Short-lived | Client timestamp |

### Akamai Cookie Pipeline

These three cookies form a dependency chain — each must exist before next is valid:

| Cookie | TTL | Role |
|--------|-----|------|
| `bm_sz` | 4 hours | Seeds PRNG for sensor_data encryption. Must exist before first sensor POST. If absent, sensor.js falls back to default seed `8888888` — a known bot signal. |
| `ak_bmsc` | 2 hours (HTTP-only) | Device-level clearance after successful sensor POST. Skips full re-evaluation within TTL. |
| `_abck` | Session-scoped | Primary bot verdict cookie. IP-bound. Contains `~0~` when Akamai signals "stop sending sensors". |

**Correct warm sequence:** Load `walmart.com` → `bm_sz` set → `sensor.js` runs → sensor_data POSTed → `ak_bmsc` + `_abck` issued → behavioral signals accumulate → `_abck` updated until `~0~` stop signal.

**`bm_sz` validation**: If `bm_sz` is absent after first page load, the session should reload the page (once) to force generation before proceeding. This is now implemented in `session_manager.py` (Issue 9 fix, 2026-04-30).

### Akamai ↔ PerimeterX Interaction

| Layer | System | Enforces At | Block Response |
|-------|--------|------------|----------------|
| Edge | Akamai Bot Manager | TLS + HTTP headers | HTTP 403 "Pardon Our Interruption" |
| Application | PerimeterX (HUMAN) | In-browser JS sensor | Redirect to `walmart.com/blocked` |

- **Independent systems** — passing Akamai does not inform PerimeterX, and vice versa. Both must be satisfied.
- **Sequential**: Akamai evaluates first at edge. A 403 means PerimeterX never ran. A `/blocked` redirect means Akamai passed but PX challenged.
- **Debugging rule**: 403 "Pardon Our Interruption" = Akamai problem (`_abck`/TLS). `/blocked` = PerimeterX problem (`_px3`). Never conflate these.

### Press-and-Hold CDP Sequence

Current implementation in `walmart/session_manager.py` is structurally correct. Required sequence:
```
mousePressed (buttons=1) → [hold loop: mouseMoved with jitter] → mouseReleased (buttons=0)
```

**Status as of 2026-04-30:**
1. ~~Hold loop sleep fixed at 150ms~~ — FIXED: randomized `random.uniform(0.08, 0.25)`
2. ~~Jitter ±1.5px uniform~~ — FIXED: cumulative random walk ±3–5px non-uniform drift
3. ~~Missing `pointerdown`/`pointerup`~~ — FIXED: `pointerDown`/`pointerUp` events added
4. ~~No minimum hold duration floor~~ — FIXED: 6.0s minimum enforced before early-exit URL check
5. **`/blocked?g=a` checkbox variant** — NOT HANDLED. `/blocked?g=a` shows checkbox instead of hold button. Treat as unresolvable; flag for future implementation.
6. **Success check only inspects URL** — `_px3` cookie presence not validated as secondary signal. Open gap.

### Bypass Approaches

**patchright** (Playwright fork with 22 AST-level patches) is the current Walmart automation library. Patchright removes CDP leaks, patches `navigator.webdriver`, and disables `Runtime.enable`. ~67% lower detection rate than standard headless Chrome.

**Recommended stealth stack for patchright:**
1. Run real Chrome (not bundled Chromium) with `HEADLESS=False` — non-headless reduces fingerprint distance significantly
2. Inject stealth JS via `cdp.page.add_script_to_evaluate_on_new_document()` to patch `navigator.webdriver`, `navigator.plugins`, `navigator.mimeTypes`, `window.chrome` (including `webstore`, `cast`, `bluetooth` sub-properties)
3. Use persistent Chrome profile (`user_data_dir`) so browser has real cookie history, localStorage, and cached assets
4. Residential US proxies only — no datacenter, no non-US
5. Warm Akamai session before drop: browse product pages, add to wishlist, navigate organically with realistic scroll and mouse interactions. Akamai's behavioral model updates in real time.
6. Keep `_px3` cookie fresh (< 50 seconds old) when entering checkout — re-warm if stale
7. Human-like delays between all actions (200–1200ms random)
8. Avoid direct URL navigation to `/cart` or `/checkout` when possible; prefer clicking UI elements
9. **Mouse path simulation**: emit 3–7 intermediate mouse positions (Bezier curve) before every click — now implemented via `_realistic_click()` (Issue 8 fix, 2026-04-30)
10. **Warmup interactions**: include scroll events and mouse movements on warmup pages, not just navigation — now implemented (Issue 7 fix, 2026-04-30)
11. **Randomize warmup sites**: select 3–5 from a pool of 8 in random order each session — now implemented (Issue 3 fix, 2026-04-30)
12. **Randomize stock check timing**: use exponential distribution (0.5–1.5s range) not fixed 1.0s intervals — now implemented (Issue 1 fix, 2026-04-30)

**What does NOT work reliably:**
- Selenium with `undetected_chromedriver` — still leaks WebDriver signals that Akamai catches
- Headless Chrome without stealth patches
- Datacenter or shared residential proxies
- Raw HTTP with `sensor_data` generation (Akamai v3 uses deployment-specific JS file hashes that change with each Walmart frontend deploy; maintaining working generator requires constant reverse engineering)

### Working Mitigations (Current — 2026-04-30)
- **patchright (Playwright fork)** — 22 AST-level patches. Current implementation.
- **Dual-tab warmup strategy** — Tab 1 (warmup tab) establishes Akamai behavioral profile (homepage → category → search only, NOT product pages to avoid PerimeterX sensitive-route triggers); Tab 2 (checkout tab) opened *after* Tab 1 warm, pre-loaded on the **product page URL** (not homepage) so React is already hydrated when purchase fires
- **Tab 2 pre-warm on product page**: saves 8–13s per purchase by eliminating cold-start hydration delay
- **Challenge solver** — `walmart/purchase_executor.py` handles press-and-hold PerimeterX challenge via CDP `dispatchMouseEvent`
- **Proxy manager** — `walmart/proxy_manager.py` rotates US residential/ISP proxies with cooldown and health tracking
- **Self-healing agent** — `walmart/self_healing_agent.py` auto-diagnoses and patches selector failures
- **Persistent Walmart profile** — `walmart-profile/` maintains session cookies and behavioral history
- **Session keep-alive** — navigate product pages during idle periods to keep behavioral score warm
- **Stealth script** (`_STEALTH_SCRIPT` in `session_manager.py`) — patches `navigator.webdriver`, `navigator.plugins`, `navigator.mimeTypes`, `navigator.languages`, `window.chrome`
- **Realistic mouse movement** — `_realistic_click()` emits 3–7 intermediate Bezier positions before every click (new, 2026-04-30)
- **Randomized stock check timing** — exponential distribution (0.5–1.5s) replaces fixed 1.0s intervals (new, 2026-04-30)
- **Randomized warmup sequence** — 3–5 sites from pool of 8, random order each session (new, 2026-04-30)
- **`bm_sz` validation** — reload on missing `bm_sz` to avoid default seed `8888888` signal (new, 2026-04-30)
- **`window.chrome` completeness** — added `webstore`, `cast`, `bluetooth` sub-properties (new, 2026-04-30)
- **Warmup page interaction** — 2–4 scroll events + mouse movement per warmup page (new, 2026-04-30)
- **`_px3` mid-checkout validation** — `needs_rewarm()` check integrated into `_confirm_shipping()` loop (new, 2026-04-30)

### Known Code Gaps (Still Open)
- `_abck` and `ak_bmsc` never explicitly checked in warm loop — only `_px3` is checked. Failed Akamai challenge appears as "no _px3" with no diagnosis.
- After proxy rotation in stock monitor workers, cookies come from browser session but `_abck` is IP-bound — workers using different IPs than browser that generated cookies will fail silently.
- `/blocked?g=a` checkbox variant not handled (only press-and-hold variant detected).
- `_px3` success check only inspects URL, not cookie presence.
- No circuit breaker on stock monitor side (purchase side has `CIRCUIT_BREAKER_FAILURES = 3`).
- No end-to-end integration test suite.

### 456 Block Recovery
1. Swap to a new US proxy
2. Re-warm Akamai session from homepage (don't jump directly to product page)
3. Re-establish `_px3` clearance (navigate checkout-adjacent pages)
4. Retry after full warm sequence

---

## Session & Auth

### Cookie Requirements for Checkout

Minimum required cookies for a valid checkout session:

| Cookie | Required For | Notes |
|--------|-------------|-------|
| `_abck` | All pages | Must be freshly generated by Akamai JS challenge |
| `ak_bmsc` | All pages | HTTP-only; set automatically by Akamai |
| `bm_sz` | Akamai sensor validation | Used to seed `_abck` re-encryption; reload page if absent |
| `_px3` | Cart + checkout pages | PerimeterX clearance; ~60s TTL on high-security pages |
| `_pxvid` | PerimeterX tracking | Persistent visitor ID |
| `auth` / `CID` | Account pages | Walmart account session token |
| `ACID` | Store selection/inventory | Locks session to specific store/location |
| `locDataV3` | Store/pricing localization | |
| `locGuestData` | Guest location | |

The `ACID`, `locDataV3`, and `locGuestData` cookies control which store's inventory and pricing you see. Must be set correctly or items may appear OOS/wrong price.

### Session Expiry

- `_px3` clearance cookie: ~60 seconds TTL on high-security pages (cart, checkout). **Must re-warm session if idle > 50 seconds before checkout attempt.**
- Walmart account session (`auth` cookie): typically 30 days for "stay signed in" sessions
- `_abck`: session-scoped but Akamai validates behavioral score continuously; session that goes idle may need to rebuild behavioral profile
- Idle session threshold: if browser has been idle > 30 minutes, re-validate by navigating to homepage before attempting purchase
- `_px3` age check: `session_manager.py` exposes `needs_rewarm()` which returns `True` if `_px3` is older than `PX3_MAX_AGE_SECONDS` (50s). `_cart_and_checkout()` calls this before navigating to cart and triggers `warm_session()` if needed.

### Tab Strategy (Dual-Tab Architecture)

- **Tab 1** (warmup tab): roams homepage, category pages, and search pages to build Akamai behavioral profile. Does NOT visit product pages (PDP) — PDPs are PerimeterX sensitive routes.
- **Tab 2** (checkout tab): opened via `open_checkout_tab(warmup_url=<product_url>)` after Tab 1 has warmed. Pre-loaded on the **target product page** (not `walmart.com`) so React is already hydrated. Tab 2 stays foregrounded (Chrome throttles background tab JS, slowing React hydration).
- On purchase: `_navigate()` detects Tab 2 is already on the product page, skips navigation, uses 2s page-ready timeout instead of 13s.

### Login Flow

```
1. Navigate to https://www.walmart.com/account/login
2. Fill: input[name="email"], input[type="email"]
3. Fill: input[name="password"], input[type="password"]
4. Click: button[type="submit"], button:has-text("Sign in")
5. Confirm redirect to /account or /
```

Two-factor authentication (SMS/email OTP) may be required on first login from new device/profile. With persistent Chrome profile, triggered only once.

### Account vs. Guest for Automation

**Account login is strongly preferred:**
- Saved address skips address entry fields entirely
- Saved card requires only CVV (3 digits vs. full 16-digit card number)
- Account session has browsing/purchase history → lower bot score
- Guest sessions start with zero behavioral history → higher initial suspicion

### Payment Info Storage

- Walmart stores card details server-side; browser never sees full PAN after saving
- CVV is **never** stored — always required fresh at checkout
- "Walmart Pay" is in-store only (QR code scan); not applicable to web checkout
- Express payment buttons (Apple Pay / Google Pay) available in payment step but require different interaction flow

---

## Quirks & Known Issues

### React SPA Behavior

- Walmart's entire checkout flow is a client-side React SPA. Page transitions don't trigger full navigation events — `page.url` may not update when sub-steps change.
- Always poll for DOM elements rather than waiting for URL changes within `/checkout`
- The buybox React component island (containing ATC button) is deferred client-side and NOT in SSR HTML. `window.__NEXT_DATA__` appearing is NOT sufficient to guarantee ATC button is in DOM. Use `_wait_for_page_ready()` which polls for both.
- DOM mutation can be slow after clicking checkout button — allow 1.5–2 seconds before querying for next step's elements
- GraphQL used extensively for stock checks and checkout state. Walmart uses persisted query hash (`data-hash` parameter) in GraphQL requests. This hash changes with each frontend deploy and must be updated periodically (see `GRAPHQL_HASH` in `config.py`).

### Dynamic CSS Classes

- Walmart's build pipeline uses CSS Modules with hashed class names (e.g., `.f7-dn4`). These change on every frontend deploy.
- **Never target elements by class name alone.** Use `data-automation-id`, `data-testid`, `data-dca-name`, `aria-label`, `name`, `type`, or `:has-text()` selectors exclusively.
- `data-automation-id` attributes are the most stable — explicitly maintained by Walmart's QA team and rarely change without deliberate DOM refactor.

### A/B Testing

- Walmart runs heavy A/B testing on checkout flow. At any time, ~10–20% of sessions may see a different:
  - ATC button text ("Add to cart" vs. "Add to Cart" vs. "Shop now")
  - Checkout page layout (single-page vs. multi-step)
  - CVV input position
- Always implement selector arrays with 3+ fallbacks. The `purchase_executor.py` pattern of iterating a list of selectors until one matches is correct.

### The `/blocked` Challenge Page

- URL: `https://www.walmart.com/blocked` (or redirect to it)
- This is Walmart's PerimeterX "hold the button" challenge page
- Appears when `_px3` cookie is missing, expired, or bot score exceeds threshold
- Solving requires: mouse-down event held for minimum ~6 seconds on specific button, followed by mouse-up
- CDP `dispatchMouseEvent` with realistic timing, Bezier movement, and micro-jitter can solve this, but HUMAN Security's behavioral analysis validates the full signed telemetry payload — not just button hold duration
- After solving, browser redirected to originally requested URL with new `_px3` cookie
- `/blocked?g=a` variant (checkbox instead of hold button) — **NOT YET HANDLED**

### The Virtual Queue

- Appears on high-demand product drops (limited-release electronics, etc.)
- Walmart uses virtual queue that holds users before allowing add to cart
- Detected by: presence of iframe or full-page component with text like "You're in line", "virtual queue", or Walmart queue service domain
- Queue typically clears in seconds to minutes; page auto-navigates when user's position reached
- **FBT (Frequently Bought Together) ATC bypass**: Adding target item via FBT module sometimes bypasses virtual queue because FBT module loads independently of queue check. Implemented in `purchase_executor.py` as `_try_fbt_add_to_cart()`.

### Rate Limiting and IP Blocking

- Walmart rate-limits at IP level. Aggressive scraping (> ~20–30 requests/minute from one IP) triggers soft block (429 or 403).
- Checkout-specific pages have stricter rate limit. Multiple rapid checkout attempts from same IP trigger PerimeterX challenge.
- After 456 block (session-level block, not IP block), rotating to new proxy insufficient on its own — account session must also be re-warmed.
- Proxy cooldown of 5 minutes after any 403/429 is reasonable minimum. `config.py` `PROXY_COOLDOWN_SECONDS = 300` reflects this.
- Error rate threshold: bench proxy if error rate exceeds 10% over last 100 requests.

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

- If item goes OOS between ATC and Place Order, Walmart shows modal or inline error at cart or review step
- Item either removed from cart or marked unavailable with "Remove" prompt
- For grocery orders: "Allow substitution" toggle per item; automation should dismiss or pre-set these
- Detection: check for `[data-automation-id="item-unavailable"]`, `[data-testid="oos-item"]`, or text containing "no longer available" or "out of stock" during cart verification

### SKU / Item ID

- Walmart item IDs (PIDs) are numeric strings, e.g., `15042474261`
- Appear in product page URL: `walmart.com/ip/<slug>/<item_id>`
- Offer IDs (OIDs) identify specific seller's listing for item; different from PID. OIDs needed for "cart spamming" and forced queue bypass techniques.
- For standard automation: PID is sufficient for ATC.

### Authorization Charges

- When adding new payment method to account, Walmart sends small authorization charge (~$1) to verify card
- Rapid addition of same card across multiple accounts triggers bank-level fraud detection
- Reuse same saved payment method across purchases rather than re-entering card details

### GraphQL API (for stock monitoring)

- Walmart uses persisted GraphQL queries for product data
- Endpoints: `https://www.walmart.com/orchestra/home/graphql/<operation_name>/<hash>`
- Primary hash (`ItemByIdBtf`): defined in `config.py` as `GRAPHQL_HASH`; may change on frontend deploy. Current static fallback: `20d116c298a901b29763c37a4aaf8b37aeb1654e4f971cd11a7fe9de2ceab027`
- ATF hash (`ItemByIdAtf`): auto-discovered at runtime by intercepting browser network traffic on Tab 2 (during purchase navigation to product page); no manual update needed
- If GraphQL hash returns 400/404, must be manually re-extracted by inspecting product page network request

### GraphQL Stock Check Details

**Endpoint pattern:**
```
POST https://www.walmart.com/orchestra/pdp/graphql/{OperationName}/{64-char-hash}/ip/{item_id}
```

**Two operations:**
| Operation | Use | Hash Source |
|-----------|-----|------------|
| `ItemByIdBtf` | Primary stock check (full product data) | Auto-discovered at runtime via CDP in `session_manager.py`; `GRAPHQL_HASH` in `walmart/config.py` is static fallback only |
| `ItemByIdAtf` | Fallback / preorder | Auto-discovered at runtime via CDP in `session_manager.py` |

**Hash change cadence:** Every Walmart frontend deploy (~2–6 weeks normally, can be daily during heavy dev cycles). No pattern — must re-extract if static fallback needed.

**How to detect a stale hash:**
- `400 Bad Request` on `/orchestra/pdp/graphql/ItemByIdBtf/` = hash changed — static fallback in `config.py` is stale; will self-correct once Tab 2 loads any product page (CDP network handler auto-discovers new hash)
- `403` on same path = Akamai/PerimeterX session invalid (different problem)
- Stock monitor silently returns no results across all known-in-stock items = silent schema drift

**Hash re-discovery (manual fallback):** Open DevTools → Network → filter `ItemByIdBtf` on any Walmart product page → grab the 64-char hex segment → update `GRAPHQL_HASH` in `walmart/config.py`.

**More durable alternative:** Use `__NEXT_DATA__` scrape from product page HTML instead of raw GraphQL. No hash dependency; returns identical availability fields. Codebase already uses this approach — prefer it over raw GraphQL calls.

---

## Open Gaps

1. **GRAPHQL_HASH staleness** — no auto-detection of stale hash (triggers silent 400 errors on stock checks when Walmart deploys). Add monitoring for 400 response rate + auto-update mechanism.
2. **`/blocked?g=a` checkbox variant** — only press-and-hold variant handled. Add detection for checkbox variant (`/blocked?g=a` URL param).
3. **`_px3` success check** — challenge solve success confirmed by URL change only; add `_px3` cookie presence as secondary signal.
4. **Akamai cookie diagnostics** — `_abck` and `ak_bmsc` never explicitly checked in warm loop. Add validation checks for debugging failed Akamai challenges.
5. **`_abck` IP-binding after proxy rotation** — stock monitor workers use different IPs than the browser that generated `_abck`; will fail silently. Consider per-worker sessions or route all stock checks through browser tab.
6. **Circuit breaker on stock monitor** — purchase side has circuit breaker, monitoring side does not. Risk of escalated detection during high-failure periods.
7. **End-to-end testing** — untested full flow from product page → confirmation on live environment.

---

## Recommended Implementation Notes

### For Patchright Automation

**Browser Configuration**
- Use real Chrome (not bundled Chromium): `BROWSER_CHANNEL = "chrome"`
- Run non-headless: `HEADLESS = False` — headless Chrome has measurably different fingerprints
- Use persistent profile directory: `user_data_dir=<path>` — maintains cookies, localStorage, behavioral history
- Set realistic window size: `--window-size=1920,1080`

**Stealth Injection**
- Patch `navigator.webdriver` to `undefined` (not `false`)
- Populate `navigator.plugins` with 3 realistic plugin objects
- Populate `navigator.mimeTypes`
- Set `navigator.languages = ["en-US", "en"]`
- Ensure `window.chrome` exists with `runtime`, `loadTimes`, `csi`, `app`, `webstore`, `cast`, `bluetooth`

**Session Warming (before drop)**
- Warm Akamai behavioral profile 10–15 minutes before drop by browsing 3–5 randomly selected sites (from a pool of 8+) in random order each session
- During warmup visits, include 2–4 scroll events and realistic mouse movement per page
- Validate `bm_sz` cookie presence after first Walmart page load; reload once if absent
- Open Tab 2 via `open_checkout_tab(warmup_url=<product_url>)` after Tab 1 has warmed — pre-loads product page on Tab 2 to eliminate cold-start hydration delay at purchase time
- Ensure `_px3` cookie is fresh (< 50 seconds old) when entering checkout — `needs_rewarm()` checks this
- Use `SESSION_VALIDATE_INTERVAL = 600` to periodically keep session alive during idle
- Force re-login after 30 minutes idle (`SESSION_MAX_IDLE = 1800`)

**Selector Strategy**
- Primary: `data-automation-id` attributes — most stable
- ATC primary: `data-automation-id="atc"` (NOT `"add-to-cart-btn"` — that is a stale secondary)
- Secondary: `data-testid`, `data-dca-name`, `name`, `type`, `aria-label`
- Tertiary: `:has-text()` with exact known strings, converted to XPath for zendriver compatibility
- Never: CSS class names (hashed, change on every deploy)
- Always maintain arrays of 3+ fallback selectors per interactive element

**Timing (Human Behavior Baseline)**
- Product page dwell: 5–20s before ATC (minimum 3s; < 3s is a bot signal)
- ATC click → cart navigation: 0.5–2s natural pause (minimum 200ms)
- Cart dwell: 3–10s before clicking Checkout (minimum 1.5s)
- CVV entry: 80–200ms inter-key delay, 50–150ms keyDown hold per digit
- Place Order: 2–5s after review page load (minimum 1s)
- All delays: use randomized ranges, never fixed values
- Stock check intervals: exponential distribution (0.5–1.5s range), not fixed 1.0s

**Mouse Movement**
- All clicks: emit 3–7 intermediate mouse positions along Bezier path before final click
- Click targets: add ±5px random offset from element center
- Post-modal-dismiss: mouse_move to randomized coordinates (400–700, 300–500), not fixed point
- Warmup pages: include scroll events to simulate reading behavior

**Proxy Requirements**
- Residential US proxies only (no datacenter, no non-US)
- Sticky proxy per checkout session — do not rotate mid-checkout
- Rotate after any 403/429 with 5-minute cooldown
- Bench proxies with > 10% error rate over last 100 requests
- Avoid Mexico, Canada proxies — known to trigger 456 blocks

**Handling `/blocked`**
- Detect by URL containing "/blocked" after any navigation
- Attempt CDP-level mouse-down-hold-mouse-up on challenge button (minimum 6s hold, Bezier movement with jitter during hold)
- If CDP solve fails, session is likely too hot — rest proxy and account, re-warm from homepage before retrying
- `/blocked?g=a` checkbox variant not yet handled — treat as unresolvable for now

**Handling Virtual Queue**
- Monitor for queue iframe/overlay before attempting ATC
- Try FBT ATC path first as it sometimes bypasses queue
- If in queue, wait for pass-through signal and re-navigate to product URL afterward

**Key Configuration Values (from `config.py`)**
- `PX3_MAX_AGE_SECONDS = 50` — re-warm session if `_px3` older than this
- `QUEUE_POLL_INTERVAL = 5` seconds
- `QUEUE_TIMEOUT = 1800` seconds (30 min max queue wait)
- `CIRCUIT_BREAKER_FAILURES = 3` — pause after 3 consecutive failures
- `CIRCUIT_BREAKER_PAUSE = 60` seconds
- `CHECKOUT_PROXY_POOL_SIZE = 12` sticky proxies for checkout
- `GRAPHQL_HASH` — persisted query hash for `ItemByIdBtf`; update when Walmart deploys new frontend

**GraphQL Hash Maintenance**
- The `ItemByIdBtf` hash changes on each Walmart frontend deploy (roughly every 2–6 weeks)
- Monitor for 400/404 on stock check requests; update `GRAPHQL_HASH` in `config.py` by inspecting live product page's network traffic
- The `ItemByIdAtf` hash is auto-discovered by intercepting Tab 2 network events at purchase time — no manual update needed

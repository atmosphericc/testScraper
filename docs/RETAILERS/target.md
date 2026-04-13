# Target Profile
## Last Researched: 2026-04-07
## Last Updated: 2026-04-10 (consolidated anti-bot content from ANTIBOT.md)

---

## Checkout Flow

### Page Sequence
1. Product page → Add to cart (ATC)
2. `target.com/cart` — Cart review
3. `target.com/checkout/start` — Checkout start (login check)
4. Checkout: address/shipping step
5. Checkout: payment step
6. Checkout: order review / place order
7. `target.com/order-confirmation` — Confirmation page

### Key Notes
- **No guest checkout** — account login is mandatory. One checkout task per account.
- SPA (React) — page transitions don't reload; URL changes via history API
- Pickup vs. ship-to-home splits early in checkout; pickup requires ZIP + store selection
- "Keep me signed in" must be checked during login to maintain long-lived sessions
- Passkey prompt appears after login — must be dismissed/bypassed

### Selectors (last verified: 2026-04-08 via @retailer-researcher — see @docs/FLOW.md for comprehensive list)

**Add to cart button (priority order):**
- `button[id^="addToCartButtonOrTextIdFor"]` ← primary confirmed in-code
- `button[data-test="addToCartButton"]`
- `button[data-testid="addToCartButton"]`
- `[data-testid*="add-to-cart"]`
- `button[data-test*="addToCart"]`
- `button[data-test*="add-to-cart"]`
- `button[data-test="chooseOptionsButton"]` ← variant/size picker entry
- Text fallback: "add to cart", "preorder", "pre-order", "add to bag", "ship it"

**Cart confirmation signals (post-ATC — any one sufficient):**
- `[data-test="add-to-cart-confirmation"]` — flyout (height > 0)
- `[data-test="cart-count"]` or `[data-testid="cart-count"]` — badge count > 0
- `[data-test="cart-drawer"]` — drawer (height > 0)
- Cart page: `[data-test="cart-item"]` or `[data-testid="cart-item"]` — items present

**Place Order button:**
- `[data-test="placeOrderButton"]` ← primary canonical selector
- `[data-testid="placeOrderButton"]`
- `#placeOrderButton`
- Text fallback: "place your order", "place order", "complete order"

**CVV modal input (priority order):**
- `input[name="cvv"]`
- `input[name="cvc"]`
- `input[id*="cvv" i]`
- `input[id*="cvc" i]`
- `input[placeholder*="CVV" i]`
- `input[placeholder*="security" i]`

**Order confirmation:**
- URL contains: `order-confirmation` (primary), `confirmation`, or `thank`
- Page text: "Thanks for your order!", "Order confirmed", "Thank you", "Your order has been placed", "Order number"

### ATC POST — `carts.target.com`

**Endpoint:**
```
POST https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY
```

**Required headers:**
- `Content-Type: application/json`
- `Accept: application/json`
- `Origin: https://www.target.com`
- `Referer: https://www.target.com/p/-/A-{TCIN}` ← must match product page
- `x-application-name: web`
- Shape Security `X-*` headers (captured via CDP `Fetch.enable` interceptor, 90s TTL)
- Credentials: `include` (sends session cookies automatically)

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

**Response status codes:**
- `200` / `201` — success
- `401` — auth token expired; wait for React to refresh, then retry
- `403 + HTML body` — Shape Security block (stale/missing headers)
- `409` / `422` — item OOS at cart API
- `424` — checkout POST rejected (see `tgt-cart-error-key` response header)

### `pre_checkout` API Call

**Endpoint:**
```
POST https://carts.target.com/web_checkouts/v1/pre_checkout?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES
```

**When it fires**: Immediately before navigating to `/checkout/start` with `keepalive: true` so request completes after navigation.

**Required?** Yes — omitting pre_checkout causes checkout form to be unpopulated, causing S&C click to fail.

### Checkout Error Codes (`tgt-cart-error-key` response header)

| Code | Meaning |
|------|---------|
| `RESERVATION_FAILURE` | Race condition — someone else bought last unit |
| `INVENTORY_NOT_AVAILABLE` | Item OOS at checkout time |
| `CART_COMPARISION_FAILURE` | Cart state mismatch (server ≠ checkout session) |

---

## Anti-Bot (F5 Shape Security)

### How Shape Works

Shape Security (F5 Distributed Cloud Bot Defense) operates as a Layer 7 reverse proxy at Target's edge. Protected endpoints:
- `POST carts.target.com/web_checkouts/v1/cart_items` — ATC
- Login endpoints (auth.target.com)
- Customer information re-entry during checkout
- High-demand product pages during drops

**Block responses:**
- **403 with HTML body**: Hard block. Shape rejected the request (bad TLS, missing sensor headers, blacklisted IP, failed ML score).
- **302 redirect**: Soft challenge — rare on Target; human interaction required.

### Sensor Headers (X-[prefix]-[letter] Pattern)

Shape's client-side JS runs a **custom CISC virtual machine** with randomized opcodes (change per deploy) that collects 35+ browser/device signals and encrypts them.

**Header structure** (prefix changes per deployment, e.g., `DQ7Hy5L1`):

| Header | Content |
|--------|---------|
| `X-[prefix]-a` | Main payload — superpack-encoded + encrypted sensor data |
| `X-[prefix]-b` | Checksum from sensor data and UUID token |
| `X-[prefix]-c` | Bundle seed (constant per script version) |
| `X-[prefix]-d` | Version identifier |
| `X-[prefix]-f` | UUID token (identifies the script bundle) |

**Encoding pipeline:**
1. Collect 35+ signals: fonts, plugins, screen resolution, GPU, CPU, mouse movement, keyboard timing, scroll, `navigator.webdriver`, canvas/WebGL fingerprints, etc.
2. Encode with SuperPack (Shape's proprietary format)
3. Encrypt with randomized `seed1` + `seed2` (regenerated per page load)
4. Base64-encode with shuffled alphabet
5. Inject into `X-[prefix]-a`; b/c/d/f carry identifiers

**TTL and reuse:**
- **90-second TTL** in codebase is correct; tokens effectively valid ~2–3 minutes max
- UUID token reuse triggers a block — Shape detects it
- Warmup tab fires dummy `carts.target.com` POST to capture fresh headers; main tab uses them for real ATC POST

**Key constraint**: Without valid headers, ATC POST returns HTTP 403 with HTML body. Sensor payload is Shape's primary gate.

### TLS Fingerprinting (JA3/JA4)

Shape's first gate is at TLS handshake — evaluated before HTTP headers.

- **JA3** hashes: TLS version + cipher suite list (order matters) + extensions (order matters) + curves + formats
- **Python libraries** (`requests`, `httpx`, `aiohttp`, raw Playwright) produce distinct non-Chrome JA3 hashes and are instantly blocked
- **HTTP/2 fingerprinting**: Shape also fingerprints HTTP/2 SETTINGS frames; pseudo-header order must match Chrome
- **Why zendriver/nodriver passes**: Drives real Chromium via CDP. Native Chrome TLS stack produces authentic JA3/JA4. No `navigator.webdriver` leak. This is the single most important evasion.
- **curl_cffi alternative**: Replicates Chrome's exact TLS for raw HTTP. Works for stock monitoring (~60–70% success) but insufficient for full checkout (no JS execution = no sensor payload).

### Device ID+ (ML-Based Persistent Device Fingerprinting)

Shape's Device ID+ is a persistent device-level identifier from:
- Canvas fingerprint (GPU rendering — hardware-specific)
- WebGL renderer/vendor strings
- Font enumeration
- Hardware concurrency / device memory
- Screen characteristics (resolution, color depth, pixel ratio)
- Audio context fingerprint
- Battery API (where available)
- Behavioral patterns accumulated over multiple sessions

**How it works**: Shape AI Cloud correlates signals across requests to build a device-level ML model. Device fingerprint persists even across proxy rotations and browser restarts if hardware is unchanged.

**Triggers for device-level block:**
- High-frequency bot activity from one device (many ATC attempts in short window)
- Mismatch between claimed browser environment and observed hardware signals
- Accumulated "bot score" over multiple sessions even if individual requests pass
- Repeated Shape-blocked requests from same device fingerprint

**Block characteristics**: Silent — requests may partially succeed but fail at checkout. Over time behavior deteriorates into hard 403s.

**Recovery**: Stop all automation, rest device 24–48 hours minimum. Persistent Chrome profile (`nodriver-profile/`) helps by accumulating legitimate-looking history, but profile history alone doesn't recover a flagged device fingerprint.

**Mitigation**: Persistent profile provides consistent canvas/WebGL fingerprints (Chrome uses same GPU render path consistently), which looks like a real person's device rather than rotating ephemeral environment.

### Device Block Recovery Protocol

1. Stop ALL tasks immediately
2. Close all browser instances
3. Rest the device 24–48 hours minimum
4. Continuing during an active block accelerates permanent ban
5. Do NOT attempt to work around with different proxies — Device ID+ is hardware-anchored, not IP-anchored

### CDP Detection — Safe vs. Risky Commands

Shape can detect CDP instrumentation through JavaScript side effects.

**Dangerous (avoid entirely):**

| Command | Risk | Why |
|---------|------|-----|
| `Runtime.enable` | CRITICAL — always detected | Causes `Runtime.consoleAPICalled` events; sites detect via JS property getters. Shape knows ~95% of automation depends on this. |
| `Page.setBypassCSP` | HIGH | Disabling CSP is a major bot signal |
| `Emulation.setUserAgentOverride` | HIGH | User-agent mismatch with feature detection caught |
| `Emulation.*` (namespace) | MEDIUM-HIGH | Any emulation override creates detectable inconsistencies |

**Safer (what current codebase does):**

| Command | Safety | Why |
|---------|--------|-----|
| `Fetch.enable` (targeted patterns) | LOW RISK | Interception does not trigger Runtime events; Shape cannot detect via JS. Codebase correctly limits to `*carts.target.com*` and checkout endpoints. |
| `Storage.getCookies` / `Network.setCookie` | LOW RISK | Cookie manipulation not detectable from page JS |
| `Input.dispatchMouseEvent` | LOW RISK | Generates realistic browser-native events |

**Current codebase analysis**: CDP usage is well-scoped. `Fetch.enable` is primary domain, correctly restricted to specific URL patterns. `Runtime.enable` not explicitly called (zendriver manages contexts internally). Risk: zendriver may internally call `Runtime.enable` during initialization — worth auditing.

### IP Requirements

| IP Type | Target ATC Success Rate | Notes |
|---------|------------------------|-------|
| Datacenter (AWS, GCP, Azure, etc.) | Very low / 0% | Immediately hard-blocked. IP reputation DBs comprehensive. |
| Datacenter proxy | Very low | Shape identifies by AS number, not just IP |
| Residential proxy (rotating) | Moderate | Works but higher latency, variable. Shape scores IP history. |
| ISP proxy (static residential) | High | Recommended. Assigned by ISPs to real customers — lowest bot score. |
| Mobile proxy (4G/5G) | High | Excellent trust score. May share IP — Shape accounts for this. |

**IP rotation behavior**: Shape ATC tokens are session-scoped but NOT IP-bound (unlike Walmart's `_abck`). However, proxy rotation mid-session risks degraded scoring — Shape AI Cloud correlates velocity and behavioral patterns across IPs within session.

**Recommendation**: Use single stable ISP proxy throughout session. Only rotate if current IP is hard-blocked.

### Known Bypass Techniques (2025–2026)

**What works:**
1. **Real browser via zendriver/nodriver** (current implementation) — authentic TLS, no `navigator.webdriver`, no WebDriver protocol leaks. Gold standard.
2. **Persistent browser profile** (`nodriver-profile/`) — cookies, localStorage, behavioral history reduce cold-session bot score
3. **CDP Fetch interception for header capture** — warmup tab fires dummy POST, main tab reuses headers. Browser itself generates valid sensor data.
4. **ISP proxies** — strong trust score, consistent IP reduces velocity signals
5. **Session warmup before ATC** — navigating Target.com before ATC lets Shape JS initialize and generate valid tokens
6. **curl_cffi for monitoring only** — RedSky bulk API not Shape-protected; avoids Shape entirely for stock checks

**What no longer works:**
- Selenium/Playwright with stealth patches (Shape detects `Runtime.enable` artifacts + JS patching inconsistencies)
- Static HTTP libraries (`requests`, `httpx`) — instantly rejected by TLS fingerprinting
- Reusing sensor headers across sessions — UUID token reuse detected
- Datacenter IPs — fully blocked
- Patching `navigator.webdriver` via `Object.defineProperty()` — Shape detects Proxy usage

**Emerging concerns (2026):**
- Shape acquired new ML-based signal correlation — behavioral patterns across multiple successful sessions now weighted heavily. Gradual degradation of success rates possible even without individual request failures.
- F5 acquired Fletch (agentic threat detection) and CalypsoAI (runtime AI security) in 2025 — trajectory suggests more dynamic, adaptive detection coming

### Shape Validation on ATC POST

**Required headers for Shape to pass:**

| Header | Value / Source |
|--------|---------------|
| `X-[prefix]-a` | Shape sensor payload from warmup tab (captured via CDP) |
| `X-[prefix]-b` | Shape checksum |
| `X-[prefix]-c` | Bundle seed |
| `X-[prefix]-d` | Version |
| `X-[prefix]-f` | UUID token |
| `x-application-name` | `web` (required for backend routing) |
| `Authorization` | Bearer token from cached auth headers (from browser's own requests) |
| `Content-Type` | `application/json` |
| `Referer` | Product page URL (must match real Target page) |
| `Origin` | `https://www.target.com` |
| `Sec-Fetch-*` headers | Must be present and consistent |

**Shape validates:**
1. TLS fingerprint (JA3/JA4) — before HTTP layer
2. Presence and syntax of all `X-[prefix]-*` headers
3. UUID token uniqueness (reuse = block)
4. Sensor payload authenticity (ML scoring)
5. IP reputation
6. Session cookie validity (`accessToken`, `visitorId`, etc.)
7. Header order and `Sec-Fetch-*`, `Accept-Language`, `Origin`, `Referer` presence

**90-second header TTL** is correct based on observed token rotation (~2 min validity). Stale tokens return 403. Warmup tab must re-fire dummy POST to refresh before main tab's real purchase attempt.

**Warmup tab strategy** (persistent background tab) continuously refreshes tokens so main purchase tab always has fresh headers.

### Working Mitigations
- **zendriver (nodriver)** — avoids WebDriver TLS and `navigator.webdriver` leak
- **CDP Fetch interception (NOT Runtime)** — `src/session/purchase_executor.py` intercepts via `cdp.fetch.enable()` on specific patterns without triggering Runtime.enable detection
- **Persistent Chrome profile** — `nodriver-profile/` maintains cookie/localStorage history, reduces cold-session bot score
- **ISP proxies** — recommended for purchasing; residential acceptable for stock monitoring (RedSky not Shape-protected)
- **Session export/restore** — `save_login.py` captures full cookie set via CDP `Storage.getCookies` for persistence
- **Warmup tab** — continuously fires dummy ATC POSTs to keep headers fresh

### Risky Code Patterns
- Any `Runtime.enable` CDP calls (check zendriver internal initialization) — reduce CDP surface to `Fetch.enable` only
- Uniform `time.sleep()` with fixed values — replace with random delays in human range (200–1200ms)
- Shape sensor headers cached beyond 90 seconds — current 90s TTL is correct but should not be relaxed
- UUID token (`X-[prefix]-f`) reuse across multiple ATC attempts — each attempt must use fresh tokens
- Reusing Shape headers on different proxy IP — Shape may correlate IP + token origin

---

## Session & Auth

### Cookie Requirements

**For ATC POST to `carts.target.com`:**

| Cookie | Required | Notes |
|--------|----------|-------|
| `guest` or `TealeafAkaSid` | Yes | Session identity / analytics anchor |
| `accessToken` / `target.visitors` | Yes | Auth session — must be valid Target login |
| `visitorId` | Yes | Unique visitor identity for RedSky and cart APIs |
| `sapphire` | Yes | A/B test / session segmentation |
| Shape ATC token (cookie or header) | Yes | Shape bot clearance for ATC endpoint |

**Cookie domain requirement**: Cookies must be on `.target.com` (root domain) to be sent to `carts.target.com`. Cookies scoped to `www.target.com` are NOT forwarded. Purchase executor handles this via `_fix_auth_cookie_domains()`.

**Shape ATC tokens:**
- **Account-independent** — same token works across any Target account (session clearance, not account credential)
- **Session-scoped** — NOT reusable across purchase attempts. Each ATC consumes a token; retries need fresh tokens.
- **Short TTL** — effective validity ~2–3 minutes. Codebase's 90-second cache threshold is correct and conservative.

**For checkout at `target.com/checkout/start`:**
- All auth cookies above, plus `cartId` (assigned by cart API on successful ATC)
- Shape also evaluates checkout page requests

### Cookie Domain Fix

All cookies must be on `.target.com` (root domain) to be sent to `carts.target.com` subdomain. `www.target.com`-scoped cookies are not forwarded. Executor's `_fix_auth_cookie_domains()` handles this.

### Session Behavior
- Sessions valid days-to-weeks if "Keep me signed in" was checked
- Token refresh happens automatically if `refreshToken` is valid
- Session invalidates on: manual sign-out, password change, account lock
- Profile stored in `nodriver-profile/` for persistence

---

## Quirks & Known Issues

### DOM/SPA
- React SPA — elements mount/unmount dynamically; wait for element presence, not page load
- `data-test` attributes are the most stable selectors (Target uses them consistently)
- ATC flow can vary: some items have "ship" vs "pickup" modal before cart

### Rate Limiting
- Heavy throttling on product API during drops; multiple API keys recommended (already implemented)
- RedSky API (`redsky_aggregations`) used for bulk stock checks — rotate keys

### High-Demand Drops
- Shape protection intensifies during high-demand drops
- Fresh Shape tokens per attempt — don't cache ATC cookies across purchase attempts
- ISP proxies strongly preferred over residential during drops

### Known Automation Notes (from existing codebase)
- `src/session/purchase_executor.py` uses CDP header interception to inject auth — do not modify
- `CARD_CVV` is hardcoded in purchase_executor.py — off-limits file, pending externalization to `.env` as `TARGET_CVV`
- `target_login.py` credentials externalized to `TARGET_EMAIL` / `TARGET_PASSWORD` env vars (fixed 2026-04-08)
- Checkout confirmation detected by URL match to `target.com/order-confirmation`

---

## Open Gaps

1. **Shape header TTL monitoring** — no alerting when 90-second cache approaches stale threshold. Add proactive refresh trigger at 60 seconds.
2. **CVV modal race condition** — no timeout/retry if modal appears after Place Order but before confirmation URL check completes. Add 12s poll timeout + retry.
3. **Circuit breaker** — no automatic pause after repeated failures. Device blocks accumulate faster if retries continue during Shape scoring decay.
4. **End-to-end testing** — untested full flow from product page → confirmation on live environment.
5. **`Runtime.enable` audit** — zendriver internals may call `Runtime.enable` during CDP initialization. Confirm it's not happening.

---

## Recommended Implementation Notes

1. **Externalize credentials**: `EMAIL`/`PASSWORD` done (2026-04-08). `CARD_CVV` in `purchase_executor.py` still hardcoded — off-limits file, pending owner action.
2. **Fresh Shape tokens per run**: Never cache ATC cookies across purchase attempts; treat as single-use.
3. **ISP proxies for all tasks**: Residential only for monitoring fallback.
4. **CDP discipline**: Minimize CDP command surface — only use what's needed; avoid `Runtime.enable` if not necessary.
5. **Account-based only**: Don't attempt guest checkout flow — it doesn't exist.
6. **Passkey dismissal**: Must handle post-login passkey/biometric prompt.
7. **`data-test` selectors**: Prefer over CSS class selectors — intentionally stable.
8. **Device rest after full block**: 24–48h minimum; no recovery shortcut.

*Flag @purchase-flow-engineer to verify and update selectors by inspecting live site.*

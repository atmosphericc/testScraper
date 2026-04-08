# Anti-Bot Knowledge Base

## Last Audited
2026-04-07 — Target Shape Security section fully populated by @retailer-researcher. Walmart Akamai + PerimeterX sections fully populated by @retailer-researcher. Run @antibot-analyst against live code for code-specific findings.

---

## Target (F5 / Shape Security)

### How Shape Security Manifests on Target.com

Shape Security (now F5 Distributed Cloud Bot Defense) operates as a Layer 7 scriptable reverse proxy deployed in-line at Target's edge. It processes every request to protected endpoints, serves obfuscated JavaScript to browsers, and relays telemetry to the Shape AI Cloud for real-time ML scoring.

**Protected endpoints on Target.com:**
- `POST carts.target.com/web_checkouts/v1/cart_items` — ATC
- Login endpoints (auth.target.com)
- Customer information re-entry during checkout
- High-demand product pages during drops

Shape does not protect static assets or general browsing — only login, ATC, and checkout flows are gated.

**Block responses:**
- **403 with HTML body**: Hard block. Shape rejected the request outright (bad TLS, missing/invalid sensor headers, blacklisted IP, failed ML score).
- **302 redirect to challenge page**: Soft challenge. Shape has low confidence — human interaction required. Rare on Target vs. login scenarios.
- Internally: `x-shape-pass` response header is emitted on Shape-evaluated requests (value indicates pass/fail verdict). The codebase already reads this header in the CDP interceptor.

---

### Sensor Headers (X-[prefix]-[letter] Pattern)

Shape injects obfuscated JavaScript onto every protected page. This JS runs a **custom CISC virtual machine** with randomized opcodes that change per script version (typically per deploy). The VM executes bytecode that collects browser/device signals and encrypts them.

**Header structure** (example prefix `DQ7Hy5L1` — prefix changes per Target deployment):

| Header | Content |
|--------|---------|
| `X-[prefix]-a` | Main payload — superpack-encoded + encrypted sensor data (base64 with shuffled alphabet) |
| `X-[prefix]-b` | Checksum derived from sensor data and UUID token |
| `X-[prefix]-c` | Bundle seed (constant for a script version, embedded in obfuscated bytecode) |
| `X-[prefix]-d` | Version identifier (constant) |
| `X-[prefix]-f` | UUID token (visible in the JS, identifies the script bundle) |

**Encoding pipeline:**
1. Collect 35+ browser/behavioral signals (two-stage: environment snapshot first, then interaction events)
2. Encode with SuperPack — Shape's proprietary binary serialization format (open spec at `shapesecurity/superpack-spec`)
3. Encrypt with a custom function using `seed1` + `seed2` (randomized per page load — rotate on every page reload)
4. Base64-encode with a shuffled alphabet (also randomized per page load)
5. Inject result into `X-[prefix]-a`; b/c/d/f carry supporting identifiers

**TTL and reuse:**
- Encryption seeds, custom alphabet, and UUID token are all regenerated on each page load
- Reusing the same UUID token (`-f`) for multiple requests triggers a block — Shape detects it
- The codebase uses a **90-second TTL** (`headers_age < 90`) for cached Shape headers — this is appropriate; tokens are effectively valid ~2 minutes at most
- Warmup tab fires a dummy `carts.target.com` POST to capture fresh headers; main tab uses them for the real ATC POST

**What the sensor data contains (35+ signals):**
- Browser environment: fonts, plugins, screen resolution, color depth, hardware concurrency, device memory, timezone
- OS/hardware enumeration: GPU (graphics driver detection), CPU, platform
- User interaction events: mouse movements, click coordinates, keyboard cadence, copy-paste actions, scroll events
- Automation markers: `navigator.webdriver`, `navigator.plugins.length`, `window.chrome` presence, CDP artifacts
- Canvas/WebGL fingerprints
- Network/timing metadata

**Key constraint**: Without headers (or with stale/invalid ones), ATC POST to `carts.target.com` returns HTTP 403 with an HTML body (Shape block). The sensor payload is Shape's primary gate for ATC.

---

### TLS Fingerprinting (JA3/JA4)

Shape's first and strongest gate is at the TLS handshake level — evaluated before any HTTP header or cookie is examined.

**What JA3 captures**: Hash of TLS version + cipher suite list (order matters) + extensions list (order matters) + elliptic curves + point formats. Python `requests`, `httpx`, `aiohttp`, and raw Playwright all produce distinct non-Chrome JA3 hashes.

**JA4 evolution**: JA4 normalizes cipher suites and extensions into canonical order before hashing, defeating simple randomization attacks that defeated JA3-based blocking.

**HTTP/2 fingerprinting**: Shape (and modern anti-bots) also fingerprint HTTP/2 SETTINGS frames — the order of pseudo-headers (`authority`, `method`, `path`, `scheme`) and window sizing must match a real Chrome profile.

**Why zendriver/nodriver passes**: zendriver drives a real Chromium instance via CDP. The TLS stack is Chrome's native stack — produces an authentic JA3/JA4. There is no WebDriver framework present so `navigator.webdriver` is absent. This is the single most important evasion for Shape on Target.

**curl_cffi alternative**: `curl_cffi` (wraps `curl-impersonate`) can replicate Chrome's exact TLS fingerprint for direct HTTP requests. Reportedly handles 60-70% of Shape-protected endpoints without browser automation — viable for stock monitoring but insufficient for full checkout (no JS execution = no sensor payload generated).

---

### Device ID+ (ML-Based Persistent Device Fingerprinting)

Shape's Device ID+ is a persistent device-level identifier built from a composite of:
- Canvas fingerprint (GPU rendering signature — hardware-specific)
- WebGL renderer/vendor strings
- Font enumeration results
- Hardware concurrency / device memory
- Screen characteristics (resolution, color depth, pixel ratio)
- Audio context fingerprint
- Battery API (where available)
- Behavioral patterns accumulated over multiple sessions

**How it works**: The Shape AI Cloud correlates signals across requests to build a device-level ML model. Even across proxy rotations and browser restarts, the device fingerprint remains stable if the hardware is unchanged.

**Triggers for device-level block**:
- High-frequency bot activity from one physical device (many ATC attempts in a short window)
- Mismatch between claimed browser environment and observed hardware signals
- Accumulated "bot score" over multiple sessions even if individual sessions pass
- Repeated Shape-blocked requests from the same device fingerprint

**Block characteristics**: Device blocks are silent — requests are not obviously 403'd immediately. Instead, Shape may let requests partially succeed (add to cart) but fail silently at checkout, or impose soft challenges. Over time behavior deteriorates into hard 403s.

**Recovery**: Stop all automation, rest the device 24-48 hours minimum. The `nodriver-profile/` persistent profile helps because it accumulates legitimate-looking browsing history — but if the device fingerprint itself is flagged, profile history alone does not recover it.

**Mitigation**: The persistent Chrome profile (`nodriver-profile/`) provides consistent canvas/WebGL fingerprints across sessions (Chrome uses the same GPU render path consistently), which actually helps — it looks like a real person's device rather than a rotating ephemeral environment.

---

### Soft Challenge vs. Hard Block

| Condition | Response | Recovery |
|-----------|----------|----------|
| Missing sensor headers entirely | Hard 403 HTML | Re-warm headers via warmup tab |
| Stale/reused UUID token | Hard 403 HTML | Refresh sensor headers |
| Datacenter IP | Hard 403 HTML | Switch to ISP/residential proxy |
| Invalid sensor payload (tampered) | Hard 403 HTML | No recovery — requires valid JS execution |
| Low confidence / first-time session | 302 to challenge page | Solve challenge manually or re-warm session |
| Device-level block accumulating | Degraded success rate, eventual hard 403 | 24-48h rest |
| Invalid or missing Shape ATC cookie (legacy mode) | ATC rejected silently | Re-generate via harvester |
| Proxy IP in Shape's blocklist | Hard 403 | Rotate proxy |

**Key insight**: Shape distinguishes between "token missing" (no JS executed = definitely a bot) and "token present but suspicious" (JS executed but behavioral signals look automated). The former is an immediate hard block; the latter results in a lower ML score that may only manifest as degraded success over time.

---

### Session Cookie Requirements

**For ATC POST to `carts.target.com`:**

| Cookie | Required | Notes |
|--------|----------|-------|
| `guest` or `TealeafAkaSid` | Yes | Session identity / analytics anchor |
| `accessToken` / `target.visitors` | Yes | Auth session — must be valid Target login cookies |
| `visitorId` | Yes | Unique visitor identity used by Target's RedSky and cart APIs |
| `sapphire` | Yes | A/B test / session segmentation |
| Shape ATC token (cookie or header) | Yes | Shape bot clearance for ATC endpoint |

**Cookie domain requirement** (documented in codebase): Cookies must be set on `.target.com` (root domain) to be sent to `carts.target.com`. Cookies scoped to `www.target.com` are NOT sent to the cart subdomain. The purchase executor already handles this via `_fix_auth_cookie_domains()` which re-injects cookies with the correct domain.

**Shape ATC tokens** are:
- **Account-independent**: The same token works across any Target account — it's a session clearance, not an account credential
- **Session-scoped**: NOT reusable across purchase attempts. Each ATC consumes a token; retries require fresh tokens
- **Short TTL**: Effective validity ~2-3 minutes from generation. The codebase's 90-second cache threshold is correct and conservative

**For checkout at `target.com/checkout/start`:**
- All auth cookies above, plus `cartId` (assigned by cart API on successful ATC)
- Shape also evaluates checkout page requests — the same sensor mechanism runs on `/checkout/start`

---

### CDP Detection — Safe vs. Risky Commands

Shape can detect CDP instrumentation through JavaScript side effects that occur when certain CDP domains are enabled.

**Dangerous CDP commands (avoid entirely)**:

| Command | Risk | Why |
|---------|------|-----|
| `Runtime.enable` | CRITICAL — always detected | Causes `Runtime.consoleAPICalled` events; websites detect it via JS property getters on Error objects and console interception. ~95% of automated frameworks depend on this — Shape knows this. |
| `Page.setBypassCSP` | HIGH | Disabling CSP is a major bot signal — anti-bots test for this explicitly |
| `Emulation.setUserAgentOverride` | HIGH | User-agent mismatch with actual browser capabilities (feature detection) is caught |
| `Emulation.*` (namespace-wide) | MEDIUM-HIGH | Any emulation override creates detectable inconsistencies |
| `Runtime.evaluate` (on main frame) | MEDIUM | Use isolated worlds instead |

**Safer CDP usage (what the codebase does correctly)**:

| Command | Safety | Why |
|---------|--------|-----|
| `Fetch.enable` (targeted patterns) | LOW RISK | Network interception via Fetch domain does not trigger Runtime events; Shape does not detect this via JS. The codebase correctly limits patterns to `*carts.target.com*` and checkout endpoints — minimal surface. |
| `Storage.getCookies` / `Network.setCookie` | LOW RISK | Cookie manipulation via CDP is not detectable from JS running in the page |
| `Network.deleteCoookies` | LOW RISK | Same as above |
| `Input.dispatchMouseEvent` | LOW RISK | Generates realistic browser-native input events |
| `cdp.fetch.continue_request` | LOW RISK | Part of Fetch interception — does not expose Runtime |

**Current implementation analysis**: The codebase's CDP usage is well-scoped. `Fetch.enable` is the primary CDP domain used, and it's correctly restricted to specific URL patterns. `Runtime.enable` does not appear to be explicitly called (zendriver manages execution contexts internally). The main risk is if zendriver internally calls `Runtime.enable` as part of its CDP initialization — this depends on zendriver internals and may be worth auditing.

**Fix for Runtime.enable if needed**: Two approaches:
1. `Page.createIsolatedWorld` — run all script injection in an isolated context, preventing page scripts from observing CDP side effects
2. Enable/Disable cycling — call `Runtime.Enable` then immediately `Runtime.Disable` to get execution context IDs while minimizing exposure window

---

### IP Requirements

| IP Type | Target ATC Success Rate | Notes |
|---------|------------------------|-------|
| Datacenter (AWS, GCP, Azure, etc.) | Very low / 0% | Immediately hard-blocked by Shape. IP reputation databases are comprehensive. |
| Data-center proxy (shared, hosting) | Very low | Same as above — Shape identifies AS number, not just IP |
| Residential proxy (rotating) | Moderate | Works but higher latency and more variable. Shape scoring considers IP history. |
| ISP proxy (static residential) | High | Recommended. Static IPs assigned by ISPs to real residential customers — lowest bot score. Proxies.sx, Brightdata ISP tier, etc. |
| Mobile proxy (4G/5G) | High | Excellent trust score. May share IP with many users — Shape accounts for this. |

**IP rotation behavior**: Shape ATC tokens are session-scoped but not IP-bound (unlike Akamai `_abck` for Walmart). However, proxy rotation mid-session still risks degraded scoring because the Shape AI Cloud correlates velocity and behavioral patterns across IPs within a session.

**Recommendation**: For the persistent browser session used for purchasing, use a single stable ISP proxy throughout the session. Only rotate if the current IP is hard-blocked.

---

### Known Bypass Techniques (2025-2026)

**What works:**
1. **Real browser via zendriver/nodriver** (current implementation): Produces authentic TLS, no `navigator.webdriver`, no WebDriver protocol leaks. Shape's primary signals (JA3, webdriver, JS API checks) are all satisfied. This is the gold standard.
2. **Persistent browser profile** (`nodriver-profile/`): Accumulated cookies, localStorage, and behavioral history reduce cold-session bot score.
3. **CDP Fetch interception for header capture**: Capturing Shape sensor headers from the browser's own ATC requests (warmup tab fires dummy POST, main tab reuses headers) is a reliable and undetectable technique — the browser itself generates valid sensor data.
4. **ISP proxies**: Strong trust score with Shape. Consistent IP reduces velocity signals.
5. **Session warmup before ATC**: Navigating Target.com before attempting ATC lets Shape JS initialize, accumulate behavioral signals, and generate valid sensor tokens.
6. **curl_cffi for monitoring only**: When only stock status is needed (RedSky API), `curl_cffi` with Chrome impersonation avoids Shape entirely because the RedSky bulk endpoint is not Shape-protected.

**What no longer works:**
- Selenium/Playwright with stealth patches (Shape detects `Runtime.enable` artifacts and JS-level patching inconsistencies)
- Static HTTP libraries (requests, httpx) — immediately rejected by TLS fingerprinting
- Reusing sensor headers across sessions — UUID token reuse detection
- Datacenter IPs — fully blocked
- Patching `navigator.webdriver` via `Object.defineProperty()` — Shape can detect Proxy usage on patched properties

**Emerging concerns (2026)**:
- Shape acquired new ML-based signal correlation capabilities — behavioral patterns across multiple successful sessions are now weighted more heavily, making gradual degradation of success rates possible even without an individual request failing
- F5 acquired Fletch (agentic threat detection) and CalypsoAI (runtime AI security) in 2025 — product integration trajectory suggests more dynamic, adaptive detection in near-term

---

### `carts.target.com` API — Shape Validation on ATC POST

**Endpoint**: `POST https://carts.target.com/web_checkouts/v1/cart_items`

**Required headers for Shape to pass**:

| Header | Value / Source |
|--------|---------------|
| `X-[prefix]-a` | Shape sensor payload (captured from warmup tab's ATC POST via CDP interceptor) |
| `X-[prefix]-b` | Shape checksum |
| `X-[prefix]-c` | Bundle seed |
| `X-[prefix]-d` | Version |
| `X-[prefix]-f` | UUID token |
| `x-application-name` | `web` (required for backend routing; added explicitly in purchase executor) |
| `Authorization` | Bearer token from cached auth headers (captured by CDP interceptor from browser's own requests) |
| `Content-Type` | `application/json` |
| `Referer` | Product page URL — must match a real Target product page |
| `Origin` | `https://www.target.com` |
| `Sec-Fetch-*` headers | Must be present and consistent (browser sends these automatically) |

**What Shape validates on ATC POST**:
1. TLS fingerprint (JA3/JA4) — evaluated before the HTTP layer
2. Presence and syntactic validity of all `X-[prefix]-*` headers
3. UUID token uniqueness (reuse = block)
4. Sensor payload authenticity (ML scoring against known-good payloads)
5. IP reputation
6. Session cookie validity (`accessToken`, `visitorId`, etc.)
7. Header order and presence of `Sec-Fetch-*`, `Accept-Language`, `Origin`, `Referer`

**The 90-second header TTL** in the codebase is correct based on observed token rotation (~2-minute validity). Stale tokens return 403. When headers are stale, the warmup tab must re-fire a dummy ATC POST to capture fresh tokens before the real purchase attempt proceeds.

**The warmup tab strategy** (persistent background tab visiting `carts.target.com`) is the correct architecture — it continuously refreshes Shape tokens so the main purchase tab always has fresh headers available.

---

### Working Mitigations
- **zendriver (nodriver)**: Avoids WebDriver TLS signature and `navigator.webdriver` leak. Current implementation uses this for Target.
- **CDP Fetch interception (NOT Runtime)**: `src/session/purchase_executor.py` intercepts `carts.target.com` requests via `cdp.fetch.enable()` — captures Shape sensor headers from the browser's own requests without triggering Runtime.enable detection.
- **Persistent Chrome profile**: `nodriver-profile/` maintains cookie/localStorage history, reducing cold-session bot score.
- **ISP proxies**: Recommended for purchasing. Residential proxies acceptable for stock monitoring (RedSky API is not Shape-protected).
- **Session export/restore**: `save_login.py` captures full cookie set via CDP `Storage.getCookies` for session persistence.
- **Warmup tab**: Background tab that continuously fires dummy ATC POSTs to keep Shape headers fresh — isolates header refresh from main purchase flow.

### Risky Code Patterns
- `src/utils/target_login.py`: Hardcoded `EMAIL`/`PASSWORD` — security risk, move to `.env`
- `src/session/purchase_executor.py`: Hardcoded `CARD_CVV = '229'` — move to `.env`
- Any `Runtime.enable` CDP calls (check zendriver internal initialization) — reduce CDP surface to `Fetch.enable` only
- Uniform `time.sleep()` with fixed values — replace with random delays in human range (200–1200ms)
- Shape sensor headers cached beyond 90 seconds — the current 90s TTL threshold is correct but should not be relaxed
- UUID token (`X-[prefix]-f`) reuse across multiple ATC attempts — each attempt must use fresh tokens
- Reusing Shape headers captured from warmup tab on a different proxy IP — Shape may correlate IP + token origin

### Cookie Requirements (Target)

| Cookie | Domain | Required For | Notes |
|--------|--------|-------------|-------|
| `accessToken` | `.target.com` | ATC + Checkout | Login session token |
| `visitorId` | `.target.com` | ATC + Checkout | Visitor identity; must be consistent |
| `TealeafAkaSid` | `.target.com` | Session continuity | Analytics/session anchor |
| `sapphire` | `.target.com` | Session | A/B / segmentation |
| `guest` | `.target.com` | Guest sessions | Used if not logged in |
| Shape ATC token | Set by JS / cookie | ATC | Account-independent; session-scoped; ~3 min TTL |

All cookies must be on `.target.com` (root domain) to be sent to `carts.target.com` subomain — `www.target.com` scoped cookies are not forwarded. The executor's `_fix_auth_cookie_domains()` handles this correctly.

### Device Block Recovery
1. Stop ALL tasks immediately
2. Close all harvesters / browser instances
3. Rest the device 24-48 hours minimum
4. Continuing tasks during an active device block accelerates permanent ban
5. Do NOT attempt to work around with different proxies — Shape's Device ID+ is hardware-anchored, not IP-anchored

---

## Walmart (Akamai Bot Manager + PerimeterX/HUMAN + Cloudflare)

### Known Detections

#### Akamai Bot Manager
- **Three-vendor stack**: Akamai (edge + JS challenge) + PerimeterX/HUMAN (behavioral + hold challenge) + Cloudflare (CDN/IP). Bypassing one does not guarantee passing the others — each evaluates independently.
- **TLS fingerprinting (JA3/JA4)**: Akamai's highest-signal detection vector as of 2026. Python HTTP libraries instantly flagged. Also fingerprints HTTP/2 SETTINGS frames — wrong pseudo-header order is caught.
- **`sensor.js` fingerprinting**: Akamai injects a ~50KB rotating JS engine that collects a 58-element signal array. The two most critical signals are **canvas fingerprint** and **mouse movement trajectory**. Encryption of the sensor_data POST is seeded from the current `bm_sz` cookie value — absent `bm_sz` falls back to detectable default seed `8888888`.
- **IP reputation**: Datacenter IPs blocked at edge (20-40% success rate). Non-US proxies → 456 block via Akamai Enhanced Proxy Detection (EPD) + GeoGuard. US residential/ISP proxies achieve 85-95% success.
- **`_abck` cookie validation**: Required on every request. Missing or invalid = 403 "Pardon Our Interruption". IP-bound — invalidated immediately on proxy rotation.
- **`bm_sz` absent**: If `bm_sz` is missing, sensor.js uses default encryption seed `8888888` — detectable by Akamai. Must exist before first sensor POST.
- **Cold session**: No browsing history → lower trust score. Warmup must use homepage → category → search (not product pages, which are PerimeterX sensitive routes).
- **Direct URL navigation**: Going to `/cart` or `/checkout` without product page warmup → immediate challenge.
- **Cart-to-checkout speed**: Transition < ~1.5 seconds → high bot score.
- **`navigator.webdriver === true`**: Akamai JS checks this explicitly.
- **Zero browser plugins**: `navigator.plugins.length === 0` is a strong bot signal.
- **Session reuse across IPs**: `_abck` is session+IP-bound; any proxy rotation requires full re-warm from homepage.
- **GraphQL hash staleness**: `GRAPHQL_HASH` changes on each Walmart frontend deploy. Stale hash = silent monitoring failure. Now auto-updated via CDP (see GraphQL section below).

#### PerimeterX / HUMAN Security
- **`_px3` TTL**: Clearance cookie has ~60s TTL on cart/checkout pages. Stale `_px3` → redirect to `walmart.com/blocked` press-and-hold challenge. TTL is longer on standard browse pages.
- **`/blocked` page**: PerimeterX challenge ("Activate and hold the button to confirm you're human"). Distinct from Akamai 403 — requires different recovery. PX App ID: `PXu6b0qd2S`.
- **Button is inside closed shadow DOM**: `#px-captcha` is the stable outer anchor. The hold button itself has randomized class/ID on every page load — do not try to select it directly. Use `getBoundingClientRect()` on `#px-captcha` and dispatch CDP events to viewport coordinates.
- **Hold duration**: Minimum ~6 seconds observed to succeed. PX collects jitter patterns *during* the hold — a static hold with no movement events is a bot signal.
- **Missing `_pxvid`**: Cold visitor with no `_pxvid` history receives stricter initial score. Persistent profile (`walmart-profile/`) provides continuity.
- **Behavioral scoring**: PerimeterX evaluates independently from Akamai. Risk score 0–100 — Walmart's checkout threshold appears aggressive (medium score = immediate challenge on Hype Sale mode).
- **What raises score**: cold session, direct nav to `/cart`/`/checkout`, `navigator.webdriver`, zero plugins, straight-line mouse movement, zero scroll/keyboard events, session age < 5s before checkout, interaction with honeypot elements.
- **`g=a` variant**: `/blocked?g=a` shows a checkbox challenge instead of press-and-hold. Current code does not detect this variant.

### PerimeterX Cookie Family

| Cookie | TTL | Role |
|--------|-----|------|
| `_px3` | ~60s on checkout, session on browse | Primary clearance token — HMAC-SHA256 signed verdict |
| `_pxvid` | Session | Persistent visitor identity — cold visitor = higher initial score |
| `_pxhd` | Session | Encrypted device fingerprint |
| `pxcts` | Short-lived | Client timestamp |

### Press-and-Hold CDP Sequence

Current implementation in `walmart/session_manager.py:849–877` is structurally correct. Required sequence:
```
mousePressed (buttons=1) → [hold loop: mouseMoved with jitter] → mouseReleased (buttons=0)
```

**Known gaps — current status (as of 2026-04-08):**
1. ~~**Hold loop sleep is fixed at 150ms**~~ — FIXED: randomized `random.uniform(0.08, 0.25)`
2. ~~**Jitter is ±1.5px uniform**~~ — FIXED: cumulative random walk ±3–5px non-uniform drift
3. ~~**Missing `pointerdown`/`pointerup`**~~ — FIXED: `pointerDown`/`pointerUp` events added around mousePressed/mouseReleased
4. ~~**No minimum hold duration floor**~~ — FIXED: 6.0s minimum enforced before early-exit URL check fires
5. **No `g=a` checkbox variant detection** — `/blocked?g=a` shows a checkbox instead of the hold button; not yet handled
6. **Success check only inspects URL** — add `_px3` cookie presence as a secondary success signal (not yet done)

### Akamai ↔ PerimeterX Interaction

| Layer | System | Enforces At | Block Response |
|-------|--------|------------|----------------|
| Edge | Akamai Bot Manager | TLS + HTTP headers | HTTP 403 "Pardon Our Interruption" |
| Application | PerimeterX (HUMAN) | In-browser JS sensor | Redirect to `walmart.com/blocked` |

- **Independent systems** — passing Akamai does not inform PerimeterX, and vice versa. Both must be satisfied.
- **Sequential**: Akamai evaluates first at the edge. A 403 means PerimeterX never ran. A `/blocked` redirect means Akamai passed but PX challenged.
- **Debugging rule**: 403 "Pardon Our Interruption" = Akamai problem (`_abck`/TLS). `/blocked` = PerimeterX problem (`_px3`). Never conflate these.

### 456 Block Recovery (Akamai geo-block)

### Akamai Cookie Pipeline
These three cookies form a dependency chain — each must exist before the next is valid:

| Cookie | TTL | Role |
|--------|-----|------|
| `bm_sz` | 4 hours | Seeds PRNG for sensor_data encryption. Must exist before first sensor POST. |
| `ak_bmsc` | 2 hours (HTTP-only) | Device-level clearance after successful sensor POST. Skips full re-evaluation within TTL. |
| `_abck` | Session-scoped | Primary bot verdict cookie. IP-bound. Contains `~0~` when Akamai signals "stop sending sensors". |

**Correct warm sequence:** Load `walmart.com` → `bm_sz` set → `sensor.js` runs → sensor_data POSTed → `ak_bmsc` + `_abck` issued → behavioral signals accumulate → `_abck` updated until `~0~` stop signal.

### Working Mitigations
- **patchright (Playwright fork)**: 22 AST-level patches to Playwright — removes CDP leaks, patches `navigator.webdriver`, disables `Runtime.enable`. ~67% lower detection than standard headless Chrome. Current implementation.
- **Dual-tab warmup strategy**: `walmart/session_manager.py` — warmup tab establishes Akamai behavioral profile (homepage → category → search only, NOT product pages) before main tab attempts purchase.
- **Challenge solver**: `walmart/purchase_executor.py` handles press-and-hold PerimeterX challenge via CDP `dispatchMouseEvent`.
- **Proxy manager**: `walmart/proxy_manager.py` rotates US residential/ISP proxies with cooldown and health tracking.
- **Self-healing agent**: `walmart/self_healing_agent.py` auto-diagnoses and patches selector failures without manual intervention.
- **Persistent Walmart profile**: `walmart-profile/` maintains session cookies and behavioral history.
- **Session keep-alive**: Navigate product pages during idle periods to keep behavioral score warm.
- **Stealth script** (`_STEALTH_SCRIPT` in `session_manager.py`): patches `navigator.webdriver`, `navigator.plugins`, `navigator.mimeTypes`, `navigator.languages`, `window.chrome` — addresses all Akamai/PerimeterX JS property checks.

### Known Code Gaps (Akamai)
- `_abck` and `ak_bmsc` are never explicitly checked in the warm loop — only `_px3` is checked. A failed Akamai challenge appears as "no _px3" with no diagnosis.
- `bm_sz` is never logged or validated — if absent after first page load, sensor.js falls back to the `8888888` default seed.
- After proxy rotation in stock monitor workers, cookies come from the browser session but `_abck` is IP-bound — workers using different IPs than the browser that generated cookies will fail silently.

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

**Hash change cadence:** Every Walmart frontend deploy (~2–6 weeks normally, can be daily during heavy dev cycles). No pattern — must re-extract if static fallback is needed.

**How to detect a stale hash:**
- `400 Bad Request` on `/orchestra/pdp/graphql/ItemByIdBtf/` = hash changed — static fallback in `config.py` is stale; will self-correct once the session browser loads any product page
- `403` on same path = Akamai/PerimeterX session invalid (different problem)
- Stock monitor silently returns no results across all known-in-stock items = silent schema drift

**Hash re-discovery (manual fallback):** Open DevTools → Network → filter `ItemByIdBtf` on any Walmart product page → grab the 64-char hex segment → update `GRAPHQL_HASH` in `walmart/config.py`.

**More durable alternative:** Use `__NEXT_DATA__` scrape from the product page HTML instead of raw GraphQL. No hash dependency; returns identical availability fields. The codebase already uses this approach — prefer it over raw GraphQL calls.

**Required cookies for any Walmart request:**
| Cookie | Why |
|--------|-----|
| `_abck` | Akamai session — absence = 403 |
| `ak_bmsc` / `bm_sz` | Akamai cache/behavioral signals |
| `_px3` | PerimeterX clearance — ~60s TTL |
| `_pxvid` / `pxcts` | PerimeterX visitor tracking |
| `auth` | Account session |
| `CID` | Session identifier |

### Risky Code Patterns
- CSS class selectors anywhere in walmart/ — Walmart hashes class names on every deploy; they break constantly. Use `data-automation-id`, `data-testid`, `aria-label`, `:has-text()` only. (**FIXED 2026-04-08**: removed `.cart-item`, `.frequently-bought-together`, class-based XPaths, and class-based PX captcha selectors from purchase_executor.py and session_manager.py)
- Any fixed `time.sleep()` values — replace with randomized human-range delays (200–1200ms). (**FIXED 2026-04-08**: `_navigate`, `_confirm_shipping`, and `_go_to_checkout` fallback delay now use `random.uniform` ranges)
- No `/blocked` or login-wall guards inside checkout step loop — PerimeterX can challenge between steps. (**FIXED 2026-04-08**: `_confirm_shipping` now checks URL at every iteration)
- Reusing `_abck` after proxy rotation — must re-warm session with new proxy
- `GRAPHQL_HASH` left stale after a Walmart frontend deploy — monitor for 400 responses on stock check requests
- Raw HTTP GraphQL calls without routing through browser `fetch()` — TLS fingerprint is wrong, all required cookies must be manually maintained

### 456 Block Recovery
1. Swap to a new US proxy
2. Re-warm the Akamai session from homepage (don't jump directly to product page)
3. Re-establish `_px3` clearance (navigate checkout-adjacent pages)
4. Retry after full warm sequence

---

## General Automation Hygiene

- **Never fixed delays**: All `time.sleep()` should use randomized ranges in human norms (200–1200ms for UI interactions, 1–3s for page transitions).
- **Navigator patching**: Always ensure `navigator.webdriver = undefined`, `navigator.plugins` populated, `window.chrome` present.
- **HTTP/2**: Use HTTP/2-capable libraries or real browser. HTTP/1.1 from automation is a signal.
- **Header order**: Browser-consistent header order. Missing `Sec-Fetch-*`, `Accept-Language`, or wrong `Referer` are bot signals.
- **TLS**: Only real Chrome (via zendriver/patchright) produces a valid JA3. Python raw HTTP is immediately flagged by both F5 and Akamai.
- **Persistent profiles**: Always use a persistent browser profile (`nodriver-profile/`, `walmart-profile/`). Cold browsers score worse.
- **Credential externalization**: All credentials (`EMAIL`, `PASSWORD`, `CARD_CVV`, `WALMART_CVV`) must be in `.env`, never hardcoded.

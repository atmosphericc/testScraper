# Target Profile
## Last Researched: 2026-04-07

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

### Selectors (last verified: never — inspect live site)
- Add to cart: likely `[data-test="addToCartButton"]` or `[data-test="shoppingCartButton"]`
- Checkout button (cart): likely `[data-test="checkout-button"]`
- Place order: likely `[data-test="placeOrderButton"]`
- Order confirmation: URL match `target.com/order-confirmation` + order number element
- **Note**: Target uses `data-test` attributes extensively — these are more stable than CSS classes

### Session & Token Requirements
- **ATC cookies (Shape)**: Required for add-to-cart, bot protection bypass, and high-demand checkout
- Tokens are **account-independent** — can be harvested separately and injected
- Cookie expiry is dynamic, set by Shape's system
- `accessToken`, `idToken` in session JSON are the critical auth tokens
- Tokens remain valid for days/weeks unless account signs out

### Interstitials to Handle
- Store pickup prompt when item has local availability
- Substitution/out-of-stock prompt during checkout
- Passkey / biometric prompt after login (dismiss)
- "Save this device?" dialog

## Anti-Bot Stack

### Vendor: F5 Shape Security
Target uses F5 Shape Security (acquired by F5 in 2020 for $1B) — one of the most sophisticated anti-bot systems in retail.

### How Shape Works
- **JavaScript VM**: Custom stack-based CISC virtual machine with randomized opcodes that rotate per session. Bytecode changes constantly — static solutions expire quickly.
- **Sensor data pipeline**:
  1. Browser signals collected by obfuscated JS
  2. Encoded with Shape's proprietary "superpack" format
  3. Encrypted with randomized seeds (seed1, seed2) that rotate per page load
  4. Base64-encoded with a shuffled alphabet
  5. Transmitted via custom HTTP headers: `X-[site-prefix]-d`, `-a` (main payload), `-f` (UUID), `-b` (checksum), `-c` (bundle seed)
- **Device ID+ fingerprinting**: Persistent device identifier using ML on collected signals
- **TLS fingerprinting**: JA3/JA4 — Python requests, standard Playwright/Selenium have distinct TLS signatures that Shape flags immediately
- **Behavioral analysis**: Mouse movement patterns, keyboard timing, interaction sequence
- **CDP detection**: `Runtime.enable` and other CDP artifacts are detectable even with "undetected" browsers

### Known Detection Triggers
- Server/datacenter IPs — flagged aggressively; ISP proxies required
- Multiple rapid requests from same IP
- Standard WebDriver/Playwright TLS signatures
- CDP artifacts from `Runtime.enable` commands
- Automation-specific browser properties left exposed
- Proxy authentication failures (407 errors) → 407 block chain
- Device-level blocks: if triggered, all tasks from that device fail for 24-48 hours

### Device-Level Block Recovery
- Stop ALL tasks immediately
- Close all harvesters
- Rest device 24-48 hours
- Continuing during a device block makes it permanent faster

### Bypass Approach (current implementation)
- zendriver (nodriver-based) avoids WebDriver detection
- CDP header interception in `src/session/purchase_executor.py` injects valid Shape tokens
- Session cookies captured via `save_login.py` and restored for each purchase
- ISP proxies required for monitoring (avoid burning residential proxies on monitoring)

### Risky Patterns to Watch
- Any `Runtime.enable` CDP calls that aren't strictly necessary
- Uniform timing (too-fast, too-regular delays)
- Missing browser fingerprint properties (`navigator.plugins`, `window.chrome`, etc.)
- Wrong `Accept-Language` header order or missing headers
- Reusing Shape tokens across sessions (they're session-scoped)

## Session & Auth

### Cookie Requirements
- Login session: `accessToken`, `idToken`, `refreshToken`, `visitorId`
- Shape ATC cookies: Required at add-to-cart time (account-independent, harvestable separately)
- Full cookie export via CDP (`Storage.getCookies`) — see `src/utils/save_login.py`

### Session Behavior
- Sessions valid days-to-weeks if "Keep me signed in" was checked
- Token refresh happens automatically if `refreshToken` is valid
- Session invalidates on: manual sign-out, password change, account lock
- Profile stored in `nodriver-profile/` for persistence

### Account vs. Guest
- **Account required** — no guest checkout path
- Saved payment methods preferred (avoid re-entering card details)
- RedCard holders: no observable checkout difference for automation

## Quirks

### DOM/SPA
- React SPA — elements mount/unmount dynamically; wait for element presence, not page load
- `data-test` attributes are the most stable selectors (Target uses them consistently)
- ATC flow can vary: some items have "ship" vs "pickup" modal before cart

### Rate Limiting
- Heavy throttling on product API during drops; multiple API keys recommended (already implemented)
- RedSky API (`redsky_aggregations`) used for bulk stock checks — rotate keys

### High-Demand Drops
- Shape protection intensifies during high-demand drops ("security goes up later in day")
- Fresh Shape tokens per attempt — don't cache ATC cookies across purchase attempts
- ISP proxies strongly preferred over residential during drops

### Known Automation Notes (from existing codebase)
- `src/session/purchase_executor.py` uses CDP header interception to inject auth — do not modify
- `CARD_CVV` is hardcoded in purchase_executor.py — off-limits file, still pending externalization to `.env` as `TARGET_CVV`
- `target_login.py` credentials externalized to `TARGET_EMAIL` / `TARGET_PASSWORD` env vars (fixed 2026-04-08)
- Checkout confirmation detected by URL match to `target.com/order-confirmation`

## Recommended Implementation Notes

1. **Externalize credentials**: `EMAIL`/`PASSWORD` done (2026-04-08). `CARD_CVV` in `purchase_executor.py` still hardcoded — off-limits file, pending owner action
2. **Fresh Shape tokens per run**: Never cache ATC cookies across purchase attempts; treat as single-use
3. **ISP proxies for all tasks**: Residential only for monitoring fallback
4. **CDP discipline**: Minimize CDP command surface — only use what's needed; avoid `Runtime.enable` if not necessary
5. **Account-based only**: Don't attempt guest checkout flow — it doesn't exist
6. **Passkey dismissal**: Must handle the passkey/biometric prompt post-login
7. **`data-test` selectors**: Prefer over CSS class selectors — they're intentionally stable
8. **Device rest after full block**: 24-48h minimum; no recovery shortcut

*Flag @purchase-flow-engineer to verify and update selectors by inspecting live site.*

# Anti-Bot Quick Reference

## Last Audited
2026-04-10 — Consolidated to eliminate duplication. Full retailer-specific details live in `@docs/RETAILERS/TARGET.md` and `@docs/RETAILERS/WALMART.md`.
2026-04-10 — ATC timing fix: 12s confirmation wait reverted to 6s/proceed-anyway. Cart cycling fix: `already_in_cart` path now calls `_verify_cart` to land on cart page before checkout.

---

## Retailer Anti-Bot Stacks

| Retailer | Vendor | Detection Vectors | Bypass Status |
|----------|--------|-------------------|---------------|
| **Target** | F5 Shape Security | TLS fingerprinting (JA3/JA4), JS sensor payload, Device ID+ ML, IP reputation, CDP artifacts | ✅ Working: zendriver + warmup tab + CDP Fetch interception |
| **Walmart** | Akamai (v2/v3) + PerimeterX/HUMAN + Cloudflare | TLS, JS sensor payload, behavioral analysis, IP reputation, HTTP/2 fingerprinting | ✅ Working: patchright + dual-tab warmup + press-and-hold challenge solver |

**Difficulty**: Target ~7/10, Walmart ~9/10 (three independent vendors)

---

## Open Gaps (Critical)

1. **Target**: Shape header TTL (~90s) not monitored — need alerting when headers approach stale
2. **Target**: CVV modal race condition — no timeout/retry if modal appears after Place Order but before confirmation check
3. **Walmart**: GraphQL hash staleness — no auto-detection of stale `GRAPHQL_HASH` (triggers 400 errors silently)
4. **Walmart**: `/blocked?g=a` checkbox variant not handled — only press-and-hold variant detected
5. **Both**: Missing circuit breaker — no pause after repeated failures (Shape device block accumulation risk)
6. **Both**: No integration test suite — untested end-to-end flows

---

## Universal Automation Hygiene

**Delays**: Never use fixed `time.sleep()`. Always randomize in human norms: 200–1200ms for UI interactions, 1–3s for page transitions.

**Browser Properties**: Ensure patched:
- `navigator.webdriver = undefined` (not `false`)
- `navigator.plugins` populated with 3+ real objects
- `navigator.mimeTypes` populated
- `navigator.languages = ["en-US", "en"]`
- `window.chrome` exists with `runtime`, `loadTimes`, `csi`, `app`

**HTTP/2**: Use only HTTP/2-capable libraries or real browser. HTTP/1.1 is a signal.

**Header Order**: Browser-consistent order required. Missing `Sec-Fetch-*`, `Accept-Language` are signals.

**TLS**: Only real Chrome (zendriver/patchright) produces valid JA3. Python raw HTTP is instantly flagged.

**Persistent Profiles**: Always use persistent browser profiles (`nodriver-profile/`, `walmart-profile/`). Cold browsers score worse.

**Credentials**: All must be in `.env`, never hardcoded: `EMAIL`, `PASSWORD`, `CARD_CVV`, `WALMART_CVV`.

---

## File Pointers

- **Full Target details** (Shape Security, sensor headers, TLS, Device ID+ recovery, CDP safety, IP requirements, bypass techniques): @docs/RETAILERS/TARGET.md
- **Full Walmart details** (Akamai, PerimeterX, `_abck` pipeline, GraphQL staleness, virtual queue, `/blocked` challenge): @docs/RETAILERS/WALMART.md
- **Checkout flow selectors & state machines**: @docs/FLOW.md

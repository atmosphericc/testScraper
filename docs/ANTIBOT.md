# Anti-Bot Quick Reference

> **Pivot note (2026-05-14):** the active anti-bot strategy is the Round 2 resilient
> stack documented in `docs/RESILIENT_STACK.md` — browser-native JA3/JA4 dispatch
> against Shape on Target's RedSky endpoint. Walmart-era audit ledger (2026-04 →
> 2026-05-11 Rounds A–E) and pre-pivot Target patches are in `docs/ANTIBOT_ARCHIVE.md`.

## Current Strategy (Target / F5 Shape)

The stock-check pipeline is the active anti-bot surface. Detection budget is
spent at the network layer (TLS fingerprint, IP reputation, request cadence)
rather than per-click behavioral signals.

- **JA3/JA4**: N persistent Chromes (one per BD ISP IP) — `tab.evaluate(fetch(...))`
  inherits the real Chrome TLS handshake. **No curl_cffi in the request path.**
- **IP reputation**: pinned per-Chrome via the local CONNECT forwarder
  (`src/proxy/local_forwarder.py`); pre-flight validator (`src/monitoring/proxy_preflight.py`)
  drops dead IPs at startup; `src/proxy/proxy_state.py` parks IPs after a 403 streak
  (`PARK_AFTER_403_STREAK=2`, `PARK_DURATION_S=10800`) and auto-recovers on the next 200.
- **Cadence**: `RESILIENT_TARGET_RPS` global rate; per-TCIN refresh = `RPS / ceil(N/28)`.
  Chrome launch staggered over `CHROME_STAGGER_TOTAL_S` (default 600s) to avoid a
  coordinated-burst signature. Cloaking-alarm loop watches for `previously-in-stock → OOS`
  flips that span the whole pool (false-positive guard for legitimate OOS).
- **Behavioral mixin**: PDP nav inside the polling tab. **Default OFF** — at 3 RPS
  aggregate with `behavioral_mix_ratio=0.10` Shape detected the pattern (7×403 burst
  at t=228s on 2026-05-13). Re-enable ≤0.02 only.

### Target purchase path
The checkout side uses the API place-order path (`TARGET_API_PLACE_ORDER=true`,
`TARGET_API_CART_CLEAR=true`) — see `docs/RETAILERS/TARGET_CHECKOUT_API.md` and
the 2026-05-06 entry in this file for live validation.

## Retailer Anti-Bot Stacks

| Retailer | Vendor | Detection Vectors | Bypass Status |
|----------|--------|-------------------|---------------|
| **Target** (active) | F5 Shape Security (cart/checkout request signing) **+ HUMAN Security / PerimeterX (storefront + RedSky, "Press & Hold" Human Challenge) — confirmed 2026-09-07** | Shape: TLS fingerprinting (JA3/JA4), JS sensor payload, Device ID+ ML, IP reputation, CDP artifacts. HUMAN: `_px3` (short-TTL trust token) / `_pxvid` (1-yr visitor id) / `_pxhd` (device hash) / `pxcts` on `.target.com`, behavioral sensor from `client.px-cloud.net`, IP/ASN reputation; a low trust score renders the Press & Hold interstitial on navigations and a 403 `{"captchaRelativeURL": "/captcha?trackingId=…"}` envelope on API reads (RedSky) | ✅ Round 2 resilient stack — 99.95% over 60 min @ 3 RPS / 3 IPs / 33 TCINs (pre-HUMAN baseline). 2026-09-04: cold pool profiles captcha-walled on every IP incl. the home IP; logged-in account profiles (earned `_px3`/`_pxvid`) read 200. Mitigation = avoid + detect + hand off (`src/session/px_challenge.py`, `TARGET_PX_CHALLENGE_GUARD`); no automated solving. |
| **Walmart** (legacy) | Akamai (v2/v3) + PerimeterX/HUMAN + Cloudflare | TLS, JS sensor payload, behavioral analysis, IP reputation, HTTP/2 fingerprinting | ⏸ Implemented in `walmart/` but not actively exercised post-pivot. See ANTIBOT_ARCHIVE.md. |

**Difficulty (reference)**: Target ~7/10, Walmart ~9/10.

## Recent Target Patches

2026-09-07 — **HUMAN Security (PerimeterX) "Press & Hold" layer CONFIRMED on Target; detection + hand-off shipped.**
Evidence: all three Target account jars (`target.json`, `target-2.json`, `target-3.json`) carry `_px2/_px3/_pxhd/_pxvid/pxcts`
on `.target.com` (`_pxvid` on primary dates from 2026-08-25); an Aug-27-2026 scraping write-up names Target's storefront
sensor as HUMAN's `humanSensor` from `client.px-cloud.net`; the 09-04 read-only browser probes got the 403 captcha envelope
on every Bright Data range AND on a fresh profile on the home IP, while the logged-in account profile on the home IP read 200.
The 09-04→09-07 home-IP sweep (2 cold sessions) then read **zero** RedSky 200s in 3.9 days — the wall is keyed to the cold
profile/device trust, not the IP alone. Shipped (flag-gated, no browser tests): `src/session/px_challenge.py` (read-only DOM
classifier + API-body classifier), sentinel guard `_px_challenge_parks_ladder` (a challenge page parks the destructive
restart/sign-out/relogin rungs and alerts — that ladder is how business + alt-1 went GUEST on 09-06/07), `px_block` labels on
ATC/place-order 403s, `[STOCK][PX-CAPTCHA]` on the trusted tab reader, `RESILIENT_FORCE_TAB_FETCH=auto`
(`src/monitoring/tab_fetch_policy.py`), BD sweep pool restored (16 IPs) with `probe_sweep_pool.bat` as the pre-drop standing
check. Boundary unchanged: the widget is for a person to press; nothing in the repo automates it.
Same night, 23:05 probe verdict: 31.105.228.245 / 31.105.93.225 / 168.158.143.27 → CAPTCHA, 72.56.171.184 → OK-DATA ×2
with the same fresh profile → the wall is IP-range reputation. Shipped on top: per-session captcha park
(`RESILIENT_CAPTCHA_PARK_S`), sweep cap usable × `RESILIENT_PER_IP_MAX_RPS`, `RESILIENT_POOL_FRESH_PROFILES`, probe
`home`/`reserve` modes (tests/test_captcha_session_park.py).

2026-05-06 — **Target Phase 4b place-order: LIVE-VALIDATED.** OBSERVE-mode run on TCIN 50270379
(single-account, N=1) placed a real order via API in 0.92s (HTTP 200, 8807-byte body). Parser at
`src/session/purchase_executor.py:3094-3140` extracted `order_id=69341e41-49a9-11f1-8a23-dd806c72f8cb`
and `reference_id=102003451858955` from `orders[0]` on first try. End-to-end checkout (navigate → ATC
→ pre_checkout → API place-order) reduced from ~12-15s (DOM) to **7.34s**. Production env:
`TARGET_API_PLACE_ORDER=true TARGET_API_CART_CLEAR=true` (drop `_OBSERVE`). Phase 6 at N≥2 still
untested live. Also: shutdown handler refactored with double-Ctrl+C escape hatch
(`app.py:3635-3705`).

2026-05-05 — **Target ATC: max-quantity from RedSky.** Hardcoded `quantity: 1` in cart POST
replaced with the per-customer purchase limit RedSky returns
(`fulfillment.maximum_order_quantity.shipping.value` or legacy `fulfillment.purchase_limit`),
capped to ATP and clamped [1, 10]. Carried as `max_qty` and plumbed through
`BulletproofPurchaseManager.start_purchase` → `PurchaseExecutor.execute_purchase(quantity=)` → ATC
fetch body. One-shot fallback to `quantity: 1` on `PURCHASE_LIMIT`/`MAX_QUANTITY`/`EXCEEDED`
(re-warms Shape headers if stale before retry). Warmup POST (line 497) still uses `quantity: 1`
(humans tap ATC once before adjusting). Files: `src/monitoring/stock_monitor.py`,
`src/purchasing/bulletproof_purchase_manager.py`, `src/session/purchase_executor.py`.

2026-04-25 — Target patches: removed `is_bot=false` from RedSky params (Shape treats explicit
self-declaration as a heuristic); UA strings updated Chrome/120 → Chrome/131; static UA pool
removed from fingerprint fallback (live `navigator.userAgent` overwrites the stored UA
post-launch); warmup POST TCIN `'00000000'` → `'81926151'` (Target $25 eGiftCard, always
available — eliminates the repeating-404 pattern signal); removed `removeAttribute('disabled')`
from the forced-click fallback. Full list in ANTIBOT_ARCHIVE.md.

## Open Gaps

1. **Resilient stack**: behavioral mixin disabled by default after 2026-05-13 finding. If
   re-enabling, use `behavioral_mix_ratio ≤ 0.02` (one nav per >15s aggregate).
2. **Target**: Shape headers reactively re-warmed on 403/block — no proactive refresh before
   TTL expiry. Recovery costs ~1-2s on the failure path. Acceptable as-is; flagged for
   awareness only. Code: `src/session/purchase_executor.py:warm_shape_headers()` (called at
   line 671 on purchase start, line 850 on Shape-block).
3. **Both retailers**: no integration test suite — end-to-end flows untested.

## Resolved (kept for history)

- **Target CVV modal race** (resolved): `_handle_cvv_modal` at
  `src/session/purchase_executor.py:1591` handles detect+fill+confirm in a single JS round-trip
  and is awaited inline before the success check. No race window remains.
- **Target fake order ID fallback** (resolved): `src/purchasing/bulletproof_purchase_manager.py:1197-1204`
  now reads `result.get('order_id')`, falls back to parsing `?orderId=` from `confirmation_url`,
  stores `None` with a warning if neither is present. Executor returns both at
  `purchase_executor.py:1217-1239`.

## Universal Automation Hygiene

**Delays**: Never use fixed `time.sleep()`. Always randomize in human norms: 200–1200ms for UI
interactions, 1–3s for page transitions.

**Browser Properties** (when running real Chrome):
- `navigator.webdriver = undefined` (not `false`)
- `navigator.plugins` populated with 3+ real objects
- `navigator.mimeTypes` populated
- `navigator.languages = ["en-US", "en"]`
- `window.chrome` exists with `runtime`, `loadTimes`, `csi`, `app`

**HTTP/2**: Use only HTTP/2-capable libraries or real browser. HTTP/1.1 is a signal.

**Header Order**: Browser-consistent order required. Missing `Sec-Fetch-*`, `Accept-Language`
are signals.

**TLS**: Only real Chrome (zendriver/patchright) produces valid JA3. Python raw HTTP is
instantly flagged. The resilient stack relies on this — *no curl_cffi in the request path*.

**Persistent Profiles**: Always use persistent browser profiles
(`state/session_profiles/`, `nodriver-profile/`). Cold browsers score worse.

**Credentials**: All must be in `.env`, never hardcoded: `EMAIL`, `PASSWORD`, `CARD_CVV`,
`WALMART_CVV`.

## File Pointers

- **Resilient stack architecture**: `docs/RESILIENT_STACK.md`,
  `docs/RESILIENT_STACK_OPERATIONAL_NOTES.md`
- **Target details** (Shape Security, sensor headers, TLS, Device ID+ recovery, CDP safety,
  IP requirements, bypass techniques): `docs/RETAILERS/target.md`,
  `docs/RETAILERS/TARGET_CHECKOUT_API.md`
- **Walmart details** (Akamai, PerimeterX, `_abck` pipeline, GraphQL staleness, virtual queue,
  `/blocked` challenge): `docs/RETAILERS/walmart.md`, `docs/RETAILERS/WALMART_CHECKOUT_API.md`
- **Checkout flow selectors & state machines**: `docs/FLOW.md`
- **Archived audit history**: `docs/ANTIBOT_ARCHIVE.md`

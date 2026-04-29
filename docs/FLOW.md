# Purchase Flow

Retailer-specific flow details, selectors, API specs, and step-by-step checkout procedures:

- **Target flow, selectors, ATC POST, pre_checkout API, step-by-step, fragile points**: @docs/FLOW_TARGET.md
- **Walmart flow, selectors, URL structure, multi-step checkout, stock check fields**: @docs/FLOW_WALMART.md

## FLAG FOR @purchase-flow-engineer (2026-04-10)

**Walmart Tab 2 hydration gate missing from `_navigate()`** — `_wait_for_page_ready()` existed in `walmart/purchase_executor.py` but was never wired into `_navigate()`. Fixed by @failure-forensics (see FAILURES.md 2026-04-10 entry). Please verify the fix integrates correctly with the FBT bypass path and the `already_in_cart` pre-check path, both of which also call `_navigate()`. The `_wait_for_page_ready()` timeout has been raised to 13000ms (was 8000ms) — see FAILURES.md 2026-04-10 entry for buybox lazy-load root cause.

**Walmart buybox lazy-load — `_wait_for_page_ready()` :has-text() gap** — `_wait_for_page_ready()` was skipping all four `:has-text()` ATC selectors due to a guard condition, leaving only CSS-attribute selectors active. CSS-attribute selectors (`data-automation-id`) are not present in the DOM until React's reconciler commits the buybox island (3-10s post SSR), so the ready check never exited early. Fixed by @failure-forensics (2026-04-10): `:has-text()` selectors now converted to XPath inside `_wait_for_page_ready()` and participate in the early-exit check. Verify: logs should now show `Page ready in X.Xs — ATC button found via: button:has-text(...)` at 2-4s rather than `Page ready timeout after 13.0s`.

## Antibot Patches Applied (2026-04-25)

**Target UA sync** — `src/session/session_manager.py`: After browser launch in `initialize()`, the live `navigator.userAgent` is now read from the running Chrome tab and stored in `fingerprint_data['user_agent']`. The static UA pool remains as the pre-launch fallback for fingerprint file persistence, but is always overwritten at runtime by the actual browser value.

**Walmart CVV keystroke timing** — `walmart/purchase_executor.py`: keyDown hold duration for CVV digit entry raised to 50–150ms (was 10–30ms). No selector changes.

**Target RedSky params** — `src/monitoring/stock_monitor.py` + `app.py`: `is_bot=false` removed from both call sites. Chrome/120 UA strings updated to Chrome/131 (last verified: 2026-04-25). These two call sites are raw HTTP (not a real browser) — they have no JA3 stealth and should be considered fragile against Shape Security escalation.

**Fragile point (new)** — The two raw-HTTP RedSky call sites (`src/monitoring/stock_monitor.py:check_stock()` and `app.py:~2460`) use Python `requests` with a spoofed UA. Shape Security can fingerprint Python TLS regardless of the UA string. If Shape tightens enforcement on the RedSky endpoint these calls will start returning 403/block responses. Mitigation: route stock checks through the browser tab (already available via `check_stock_via_tab()` in stock_monitor.py) or use zendriver for API calls.


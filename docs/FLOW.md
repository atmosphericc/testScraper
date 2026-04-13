# Purchase Flow

Retailer-specific flow details, selectors, API specs, and step-by-step checkout procedures:

- **Target flow, selectors, ATC POST, pre_checkout API, step-by-step, fragile points**: @docs/FLOW_TARGET.md
- **Walmart flow, selectors, URL structure, multi-step checkout, stock check fields**: @docs/FLOW_WALMART.md

## FLAG FOR @purchase-flow-engineer (2026-04-10)

**Walmart Tab 2 hydration gate missing from `_navigate()`** — `_wait_for_page_ready()` existed in `walmart/purchase_executor.py` but was never wired into `_navigate()`. Fixed by @failure-forensics (see FAILURES.md 2026-04-10 entry). Please verify the fix integrates correctly with the FBT bypass path and the `already_in_cart` pre-check path, both of which also call `_navigate()`. The `_wait_for_page_ready()` timeout has been raised to 13000ms (was 8000ms) — see FAILURES.md 2026-04-10 entry for buybox lazy-load root cause.

**Walmart buybox lazy-load — `_wait_for_page_ready()` :has-text() gap** — `_wait_for_page_ready()` was skipping all four `:has-text()` ATC selectors due to a guard condition, leaving only CSS-attribute selectors active. CSS-attribute selectors (`data-automation-id`) are not present in the DOM until React's reconciler commits the buybox island (3-10s post SSR), so the ready check never exited early. Fixed by @failure-forensics (2026-04-10): `:has-text()` selectors now converted to XPath inside `_wait_for_page_ready()` and participate in the early-exit check. Verify: logs should now show `Page ready in X.Xs — ATC button found via: button:has-text(...)` at 2-4s rather than `Page ready timeout after 13.0s`.

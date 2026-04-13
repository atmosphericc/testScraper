# Target.com Fixes — 2026-04-10

## Summary
Fixed 2 critical Target purchase flow issues identified in code audit:

### 1. ✅ Order ID + Confirmation URL Capture (BLOCKING)
**Status:** FIXED  
**File:** `src/session/purchase_executor.py` lines 1101-1112  
**Impact:** Order tracking now captures real order IDs from confirmation URL

**What was broken:**
- Success return dict had no `order_id` or `confirmation_url` keys
- Fallback in `bulletproof_purchase_manager.py` logged "None" or generated fake IDs
- Purchase history lost real order numbers

**What changed:**
- Extract `order_id` from confirmation URL query param (`?orderId=XXX`)
- Return both `'order_id'` and `'confirmation_url'` in success dict
- `bulletproof_purchase_manager.py` now receives real IDs from executor instead of falling back

**Example flow:**
```
Confirmation URL: https://www.target.com/order-confirmation?orderId=ad9207a1-1c86-11f1-b49a-0b675f1e8af4&ref=...
Extracted: ad9207a1-1c86-11f1-b49a-0b675f1e8af4
Logged: "Purchase successful: Product Name - Order: ad9207a1-1c86-11f1-b49a-0b675f1e8af4"
```

---

### 2. ✅ Proactive Shape Header TTL Monitoring (RELIABILITY)
**Status:** FIXED  
**File:** `src/session/purchase_executor.py` lines 594-604  
**Impact:** Prevents silent 403 Shape blocks during long purchase flows

**What was broken:**
- Shape Security tokens rotate ~every 90-120s
- Code detected stale headers (age > 90s) but reused them anyway in ATC POST
- Resulted in silent 403 HTML blocks (Shape gateway rejection)
- No alerting or recovery mechanism

**What changed:**
- Before ATC POST, check if cached headers approaching TTL (age > 60s)
- If so, proactively refresh warmup tab via `warm_shape_headers()`
- Captures fresh headers before ATC attempt
- Logs header age after refresh for visibility

**Example flow:**
```
Headers age: 55s  → use cached headers (safe)
Headers age: 61s  → REFRESH warmup tab → capture fresh headers → ATC succeeds
Headers age: 90s  → (would have failed) now prevented by proactive refresh
```

---

## CVV Modal Race Condition (Verified Correct ✅)
**Status:** No fix needed  
**File:** `src/session/purchase_executor.py` lines 2618-2625

Already handles correctly per FLOW_TARGET.md specs:
- Polls for CVV modal **immediately** after Place Order click
- 12-second timeout allows full challenge flow
- Checks for confirmation page **first** (if modal doesn't appear)
- Retry logic for busy modals

---

## Testing Checklist
- [ ] Run Target purchase flow end-to-end
- [ ] Verify order IDs logged in activity log (not "None" or "REAL-XXXXX" fakes)
- [ ] Check `logs/purchase_states.json` contains real order IDs
- [ ] Monitor Shape header ages in console during multi-item purchases
- [ ] Verify warmup refresh fires when headers approach 60s

---

## Related Docs Updated
- FAILURES.md: Open action resolved
- ANTIBOT.md: Shape header TTL gap addressed

## Files Changed
- `src/session/purchase_executor.py` (+24 lines)

## Commit
bed114cd - "Fix Target purchase flow: capture order_id + confirmation_url, proactive Shape header TTL refresh"

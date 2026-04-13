# Fixes Applied — Final Status 2026-04-10

## Summary
- **7 issues fixed** (Target: 4, Walmart: 3)
- **4 issues remain** (Walmart only: 2 critical, 2 high)

---

## ✅ FIXED (7 Total)

### TARGET — 4 Fixed

| Issue | File | Fix | Commit |
|-------|------|-----|--------|
| Order ID + Confirmation URL not captured | `src/session/purchase_executor.py` 1101-1112 | Extract from URL (`?orderId=`), return in success dict | `bed114cd` |
| Shape header TTL not monitored | `src/session/purchase_executor.py` 594-604 | Proactive refresh at 60s threshold (before ATC) | `bed114cd` |
| CVV confirm button poll timeout too short | `src/session/purchase_executor.py` 1574-1610 | 500ms fallback click attempt after initial poll | `c3eea8ea` |
| pre_checkout missing Referer header | `src/session/purchase_executor.py` 960 | Already implemented at line 960 | — |

### WALMART — 3 Fixed

| Issue | File | Fix | Commit |
|-------|------|-----|--------|
| XPath :has-text() case-sensitive | `walmart/purchase_executor.py` 841 | Case-insensitive XPath with `translate()` | `0a52cfe4` |
| Order ID fallback to placeholder string | `walmart/purchase_executor.py` 616 | Return `None` instead of `"CONFIRMED_NO_ID"` | `0a52cfe4` |
| Order ID extraction no logging | `walmart/purchase_executor.py` 611-622 | Log warnings with URL for operator visibility | `0a52cfe4` |

---

## 🔴 REMAINING CRITICAL (2)

### 1. Walmart: /blocked Checkbox Variant (`g=a`)
**Files:** `walmart/session_manager.py` (809-811), `walmart/purchase_executor.py` (960-971)  
**Status:** NOT FIXED  
**Priority:** CRITICAL — Blocks checkout

**What Happens:**
- PerimeterX serves two variants: press-and-hold (handled) or checkbox (not handled)
- Code detects `/blocked?g=a` but has no solver
- Continues to Place Order → 20s timeout → fails silently

**Fix Needed:** Implement checkbox solver or return early + log clear error

---

### 2. Walmart: GraphQL Hash Staleness (Silent 400s)
**Files:** `walmart/config.py`, `walmart/stock_monitor.py`  
**Status:** NOT FIXED  
**Priority:** CRITICAL — Breaks stock monitoring

**What Happens:**
- Hash hardcoded, no 400 error detection
- Walmart deploys → hash stale → all GraphQL queries return 400
- No error detection → assumes OOS → infinite retry loop
- Stuck until manual app restart

**Fix Needed:** Detect 400 responses, auto-rotate GRAPHQL_HASH or signal refresh

---

## 🟠 REMAINING HIGH (2)

### 3. Walmart: /blocked Mid-Checkout (Late Challenge)
**Files:** `walmart/purchase_executor.py` (700-726, 727-760)  
**Status:** NOT FIXED  
**Priority:** HIGH — Reliability gap

**What Happens:**
- Challenge can fire during CVV entry or after Place Order click
- No `/blocked` URL checks in these functions
- Page hangs → confirmation timeout → fails

**Fix Needed:** Add `/blocked` detection in `_enter_cvv_if_needed()` and post-`_place_order()` loop

---

### 4. Walmart: `_px3` Staleness Pre-Checkout
**Files:** `walmart/session_manager.py`, `walmart/purchase_executor.py`  
**Status:** NOT FIXED  
**Priority:** HIGH — Reliability gap

**What Happens:**
- PerimeterX clearance expires ~60s
- Warmup refreshes `_px3` but no guarantee it's fresh at checkout time
- Long product page browse → `_px3` stale by Place Order → challenge fires

**Fix Needed:** Check `_px3` age before checkout nav; refresh if >40s old

---

## Commits

| Commit | Files | Changes |
|--------|-------|---------|
| `bed114cd` | `src/session/purchase_executor.py` | Order ID capture, Shape header TTL refresh |
| `c3eea8ea` | `src/session/purchase_executor.py` | CVV confirm button fallback polling |
| `0a52cfe4` | `walmart/purchase_executor.py` | XPath case-insensitive, Order ID extraction |

---

## Next Steps

**Critical (Blocks operation):**
1. Implement Walmart checkbox variant handler
2. Detect GraphQL 400 responses + hash rotation

**High (Improves reliability):**
3. Add /blocked checks mid-checkout
4. Monitor _px3 age pre-checkout

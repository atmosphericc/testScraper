# Remaining Issues — 2026-04-10

After Target fixes completed. All following issues are **Walmart-specific**.

---

## 🔴 CRITICAL (Blocking)

### 1. Walmart: /blocked Checkbox Variant (`g=a`) Not Handled

**Files:** `walmart/session_manager.py` (809-811), `walmart/purchase_executor.py` (960-971)  
**Impact:** Checkout fails silently when PerimeterX checkbox challenge variant appears

**What Happens:**
- PerimeterX can serve two challenge variants: press-and-hold (handled) or checkbox (not handled)
- Code detects `/blocked?g=a` (checkbox variant) and returns `False` immediately
- `purchase_executor` receives `False`, returns `True` (was blocked, couldn't solve)
- Continues to Place Order → waits 20s for confirmation → times out → purchase fails

**Root Cause:**
- Only press-and-hold challenge is implemented
- Checkbox variant requires completely different solving mechanism (click checkbox, wait for unblock)

**Fix Needed:** Implement checkbox handler or graceful abort that prevents continuation to Place Order

---

### 2. Walmart: GraphQL Hash Staleness Not Detected (Silent 400s)

**Files:** `walmart/config.py`, `walmart/stock_monitor.py`  
**Impact:** Stock monitoring fails completely during Walmart deploys; infinite retry loops

**What Happens:**
- `GRAPHQL_HASH` hardcoded in config
- Walmart deploys new GraphQL endpoint → old hash becomes stale
- Stock monitor fires GraphQL queries with stale hash → all responses 400
- No error detection → assumes 400 means OOS → retries forever
- Never recovers until app manually restarted

**Root Cause:**
- Hash changes on every Walmart deploy
- No 400 error handling or auto-rotation mechanism

**Fix Needed:** 
1. Detect 400 responses in stock monitor
2. Auto-rotate `GRAPHQL_HASH` or signal session manager to refresh
3. Log failure so operators see the issue

---

## 🟠 HIGH (Reliability)

### 3. Walmart: Uncaught `/blocked` Mid-Checkout (Late Challenge)

**Files:** `walmart/purchase_executor.py` (700-726, 727-760)  
**Impact:** If PerimeterX challenge fires after `_confirm_shipping()` but before `_place_order()`, purchase fails

**What Happens:**
- `_confirm_shipping()` completes at line 698
- `_enter_cvv_if_needed()` runs (lines 700-726) — if `/blocked` appears here, **no detection**
- `_place_order()` runs (lines 727-760) — if `/blocked` appears here, **not caught**
- Page navigates somewhere → confirmation check times out → purchase fails

**Fix Needed:** Add `/blocked` URL checks in `_enter_cvv_if_needed()` and after `_place_order()` before waiting for confirmation

---

### 4. Walmart: `_px3` Cookie Staleness Pre-Checkout

**Files:** `walmart/session_manager.py`, `walmart/purchase_executor.py`  
**Impact:** PerimeterX clearance expires ~60s; if product page browse is long, `_px3` stale at Place Order

**What Happens:**
- Warmup tab refreshes `_px3` cookie (~60s expiry)
- User browses product pages for 45s+
- Navigate to checkout — `_px3` now ~45s old, approaching expiry
- Place Order triggers Akamai check → sees stale `_px3` → challenge fires mid-payment → fail

**Root Cause:**
- No `_px3` age tracking
- No pre-checkout refresh guarantee

**Fix Needed:** Check `_px3` age before checkout navigation; refresh if >40s old

---

## 🟡 MEDIUM (Data Quality)

### 5. Walmart: Order ID Extraction Falls Back to String

**File:** `walmart/purchase_executor.py` (762-785)  
**Impact:** If order confirmation page structure changes, loses real order number

**What Happens:**
```python
# Current behavior:
order_id = await self._extract_order_id()
# Returns:
# - "123456789" (success)
# - "CONFIRMED_NO_ID" (selector fail) ← loses real order, can't lookup purchase
# - None (no confirmation page)
```

**Problem:**
- Multiple selector/URL parsing attempts
- If all fail, returns placeholder string `"CONFIRMED_NO_ID"`
- Order tracking broken — can't look up purchase later

**Fix Needed:** 
1. Log clear warning if extraction fails
2. Add GraphQL order query fallback (query order by session/timestamp)

---

## 🟢 LOW (Hygiene)

### 6. Walmart: XPath `:has-text()` Conversion Case-Sensitive

**File:** `walmart/purchase_executor.py` (1003-1008)  
**Impact:** Misses buttons with unexpected capitalization (e.g., "CONTINUE" vs "Continue")

**What Happens:**
```python
# Selector: button:has-text("Continue")
# Converted to: //button[contains(., "Continue")]
# Misses: //button containing "CONTINUE" or "continue"
```

**Problem:**
- `contains()` is case-sensitive
- Walmart A/B tests text capitalization
- Selector fails if case doesn't match exactly

**Fix Needed:** Use case-insensitive XPath:
```xpath
//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]
```

---

## Summary Table

| # | Issue | Severity | Impact | Status |
|---|-------|----------|--------|--------|
| 1 | Checkbox variant handler | 🔴 | Checkout fails on checkbox challenge | NOT FIXED |
| 2 | GraphQL hash staleness | 🔴 | Stock monitor infinite loop | NOT FIXED |
| 3 | `/blocked` mid-checkout | 🟠 | Late challenge uncaught | NOT FIXED |
| 4 | `_px3` pre-checkout | 🟠 | Stale clearance at Place Order | NOT FIXED |
| 5 | Order ID fallback string | 🟡 | Order tracking broken on selector fail | NOT FIXED |
| 6 | XPath case-sensitive | 🟢 | Button misses on capitalization mismatch | NOT FIXED |

---

## Target Fixes Completed ✅

1. **Order ID + Confirmation URL capture** — `src/session/purchase_executor.py` lines 1101-1112
2. **Proactive Shape header TTL refresh** — `src/session/purchase_executor.py` lines 594-604
3. **CVV confirm button fallback polling** — `src/session/purchase_executor.py` lines 1574-1610
4. **pre_checkout Referer header** — Already in place at line 960

Commits: `bed114cd`, `c3eea8ea`

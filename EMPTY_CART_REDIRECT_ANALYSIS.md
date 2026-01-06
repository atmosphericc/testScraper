# Analysis: Empty Cart Redirect Issue - Add-to-Cart Fails After First Cycle

## Problem Statement

After the first successful purchase cycle, subsequent cycles fail with:
- Add-to-cart appears to click successfully
- Browser shows empty cart page
- No items in cart
- Purchase fails

## Investigation Summary

### What We Thought Was Happening

Based on user report: "it redirects to the empty cart page when it was on the product page for some reason"

We initially believed:
1. Add-to-cart click succeeds (stays on product page)
2. Target.com redirects to `/cart` page
3. Cart is empty (add-to-cart actually failed)
4. Code doesn't detect this failure

### What Actually Happened (From Logs)

**First Purchase Cycle (T+0 to T+60s):**
```
[PURCHASE_EXECUTOR] [OK] ✅ Add-to-cart button clicked (Bézier curve)
[PURCHASE_EXECUTOR] [NETWORK] Waiting for network idle...
[PURCHASE_EXECUTOR] [NETWORK] ⚠️ Network idle timeout (continuing anyway): Timeout 15000ms exceeded.
[PURCHASE_EXECUTOR] [NETWORK] Waiting 3s for Target's cart API to complete...
[PURCHASE_EXECUTOR] [REDIRECT_CHECK] Current URL after add-to-cart: https://www.target.com/p/-/A-94721096
[PURCHASE_EXECUTOR] [TEST] ✅ Checkout accessible - SUCCESS!
[PURCHASE_EXECUTOR] [TEST] Clearing cart...
... continues running ...
[REAL_PURCHASE_THREAD] [ERROR] Purchase execution timed out after 60s
[PURCHASE]  REAL purchase failed: 94721096 - execution_timeout
```

**Key Finding**: First purchase **timed out at 60 seconds** while still executing. It never completed normally.

**After Timeout (T+60 to T+84s):**
```
WARNING:src.session.purchase_executor:⚠️ Could not find any remove buttons in cart
ERROR:src.session.purchase_executor:[TEST] CRITICAL: Cart clearing failed - cannot cycle
```

The first purchase's cleanup code (cart clearing) continues running in exception handler/finally block for another 24 seconds.

**Second Purchase Cycle (T+84s):**
```
[CYCLE] 94721096: failed -> ready (was order: N/A)
[TEST_MODE_RESET] ✅ 94721096: 'failed' → 'ready' (will re-attempt next cycle)
[PURCHASE] Starting REAL purchase: ... (TCIN: 94721096)
[PURCHASE_EXECUTOR] [TARGET] Starting purchase for TCIN 94721096
[PURCHASE_EXECUTOR] [STATE] Current page URL before purchase: https://www.target.com/
...
WARNING:src.session.purchase_executor:⚠️ Could not find any remove buttons in cart  ← FROM FIRST PURCHASE!
ERROR:src.session.purchase_executor:[TEST] CRITICAL: Cart clearing failed - cannot cycle  ← FROM FIRST PURCHASE!
```

**The Race Condition**: Second purchase starts while first purchase's cleanup code is still running!

## Root Cause: Cleanup Code Running After Purchase Marked Failed

### The Problem

1. First purchase times out after 60 seconds
2. Purchase is marked as 'failed' and removed from `_active_purchases`
3. **BUT** - exception handler or finally block continues executing cart cleanup
4. Cleanup code takes 20-30 seconds to complete
5. Before cleanup finishes, purchase is reset to 'ready' and new purchase starts
6. Both purchases use same browser page → navigation conflicts

### Why This Happens

**File**: `src/session/purchase_executor.py`

The purchase execution runs in a thread with a 60-second timeout:
```python
# bulletproof_purchase_manager.py line ~1800
result = await asyncio.wait_for(
    self.purchase_executor.execute_purchase(tcin),
    timeout=60.0
)
```

When timeout occurs, the purchase executor's exception handler or finally block tries to clean up (clear cart). This cleanup is NOT cancelled when the timeout fires - it keeps running even after the purchase has been marked as failed.

### Timeline

```
T+0s:    First purchase starts (status: attempting)
T+35s:   Clicks add-to-cart successfully
T+35-60s: Stuck waiting for network idle
T+60s:   Purchase timeout fires → status changes to 'failed'
T+60s:   Exception handler begins cart clearing
T+60-84s: Cart clearing code still running (not cancelled!)
T+84s:   Second API cycle happens
T+84s:   First purchase reset from 'failed' to 'ready'
T+84s:   Second purchase starts
T+84s:   ⚠️ CONFLICT: Two operations using same browser page!
```

## Why Concurrency Check Didn't Prevent This

The concurrency check in `bulletproof_purchase_manager.py` lines 1300-1324 checks for purchases with status='completing'.

But in this case:
1. First purchase had status='attempting' (not 'completing') when it timed out
2. After timeout, status changed to 'failed'
3. Purchase was removed from `_active_purchases`
4. Concurrency check saw no active purchases
5. New purchase was allowed to start
6. **Cleanup code from first purchase was still running in background!**

## The Fix We Implemented (But Wrong Problem)

We added empty cart detection at lines 389-433 in `purchase_executor.py`:

```python
if 'cart' in current_url.lower() and 'checkout' not in current_url.lower():
    print(f"[PURCHASE_EXECUTOR] [REDIRECT_ERROR] ⚠️ Redirected to cart page after add-to-cart!")
    # Check if cart is empty
    # ... validation code ...
```

**This fix addresses the SYMPTOM (empty cart) but not the ROOT CAUSE (timeout + cleanup overlap).**

## The Real Fix Needed

### Option 1: Ensure Cleanup Completes Before Marking Purchase Failed

Wrap cleanup in try-finally and ensure it completes before the purchase thread terminates:

```python
# In purchase_executor.py
try:
    result = await execute_purchase_flow(tcin)
except asyncio.TimeoutError:
    logger.error("Purchase timed out")
finally:
    # CRITICAL: Cleanup must complete before thread exits
    await cleanup_cart()  # This MUST finish before return
    # Only after cleanup completes, allow new purchase
```

### Option 2: Check for Running Cleanup Before Starting New Purchase

Add another state like 'cleanup_in_progress':

```python
# In bulletproof_purchase_manager.py
if active_tcin in self._cleanup_in_progress:
    print(f"[PURCHASE_CONCURRENCY] Cleanup still running for {active_tcin}")
    # Wait for cleanup or skip this cycle
```

### Option 3: Don't Clear Cart in TEST_MODE

Since TEST_MODE doesn't actually complete purchases, cart clearing is optional:

```python
# Lines 486-497 and 1975-1986 in purchase_executor.py
if self.test_mode:
    # Skip cart clearing in TEST_MODE
    # Cart can accumulate items - manually clear between sessions if needed
    pass
```

This trades cart cleanliness for speed and eliminates the cleanup overlap issue entirely.

### Option 4: Increase Timeout and Wait for Network Idle Properly

The real issue is that the purchase is timing out. If we fix the network idle wait, the purchase wouldn't timeout:

```python
# Increase timeout from 60s to 90s
result = await asyncio.wait_for(
    self.purchase_executor.execute_purchase(tcin),
    timeout=90.0
)

# And/or: Improve network idle detection
# Instead of fixed 15s timeout, use adaptive waiting
```

## Impact of Our Empty Cart Detection Fix

The fix we implemented WILL help detect when add-to-cart genuinely fails (Target redirects to empty cart). But it won't solve the race condition between purchase cleanup and new purchase start.

However, it's still valuable because:
1. It detects actual add-to-cart failures
2. It fails fast instead of continuing with empty cart
3. It provides better error messages
4. It may help in cases where Target.com actually rejects the add-to-cart

## Recommendations

1. **Immediate**: Implement Option 3 (skip cart clearing in TEST_MODE) - simplest fix
2. **Short-term**: Implement Option 4 (increase timeout) - addresses root cause of timeout
3. **Long-term**: Implement Option 1 (ensure cleanup completes) - robust solution
4. **Keep**: Empty cart detection fix (lines 389-433) - still valuable for actual failures

## Testing Plan

1. Implement Option 3 first (comment out TEST_MODE cart clearing)
2. Run 3-5 consecutive purchase cycles
3. Verify no "Could not find remove buttons" errors during purchase start
4. Verify purchases complete successfully without navigation conflicts
5. If successful, consider Option 4 to address timeouts
6. Monitor for actual add-to-cart failures to validate empty cart detection

## Related Documents

- `CART_CLEARING_RACE_FIX.md` - Polling loop fix (related issue)
- `PURCHASE_OVERLAP_RACE_FIX.md` - Concurrency detection (related issue)
- `EMPTY_CHECKOUT_HANG_FIX.md` - Redirect detection (related fix)
- `temporal-sparking-whisper.md` - Original plan for empty cart detection

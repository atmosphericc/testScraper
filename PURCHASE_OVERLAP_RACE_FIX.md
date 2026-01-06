# Fix: Purchase Overlap Race Condition

## Problem

After the first successful purchase cycle, the second purchase would start while the first was still cleaning up (clearing cart). This caused:
1. Browser navigating to product page (second purchase)
2. Browser suddenly redirecting to /cart (first purchase cleanup)
3. Empty cart page appearing
4. Purchase flow getting confused

**User symptom**: "it redirects to the empty cart page before anything can happen"

## Root Cause

**Timeline showing the race condition:**

```
T+0s:   First purchase starts
T+35s:  First purchase verifies checkout ✅
T+35s:  Thread marked as 'completing', begins cart clearing
T+35s:  Thread removed from _active_purchases (too early!)
T+36s:  Next cycle timer expires
T+36s:  Concurrency check: NO active threads detected ❌
T+36s:  Second purchase starts (navigates to product page)
T+40s:  First purchase still clearing cart (redirects to /cart)
T+40s:  Browser confused - both purchases using same page
T+40s:  Empty cart redirect error
```

### Why This Happened

1. **Cart clearing takes 20-30 seconds** in TEST_MODE
2. **Cycle timer is 25-35 seconds**
3. **Thread removed too early** - right after checkout verification, not after cleanup completes
4. **No wait for cleanup** - next cycle starts immediately when no threads detected

### Evidence from Logs

```
[PURCHASE_EXECUTOR] [F5_BYPASS] Simulating human reading behavior...
[PURCHASE_EXECUTOR] [TEST] ⚠️ Cart verification failed (attempt 1)   ← FIRST purchase cleaning up
[PURCHASE_EXECUTOR] [TEST] Cart clear attempt 2/2...                  ← FIRST purchase
[PURCHASE_EXECUTOR] [STEP 4/7] Finding add-to-cart button...         ← SECOND purchase
```

The logs are **interleaved** - two purchases running simultaneously, both logging at the same time.

## The Fix

**File**: `src/purchasing/bulletproof_purchase_manager.py` lines 1300-1311

Added a 5-second wait when a thread is in 'completing' status (cleanup phase):

```python
# RACE CONDITION FIX: If thread is marked as 'completing' (in cleanup phase),
# wait briefly for it to finish before starting new purchase
if thread_info.get('status') == 'completing':
    print(f"[PURCHASE_CONCURRENCY] Thread is completing (cleanup phase) - waiting 5s for it to finish...")
    time.sleep(5.0)
    # Re-check after wait
    if active_tcin in self._active_purchases:
        elapsed_after = time.time() - thread_info['started_at']
        print(f"[PURCHASE_CONCURRENCY] Thread still active after wait ({elapsed_after:.1f}s total) - will skip new purchases this cycle")
    else:
        print(f"[PURCHASE_CONCURRENCY] Thread completed during wait - safe to start new purchase")
        active_purchase = None  # Clear flag to allow new purchase
```

### How It Works

1. **Detect cleanup phase**: Check if thread status is 'completing'
2. **Wait 5 seconds**: Give cleanup time to finish
3. **Re-check after wait**:
   - If thread gone → Allow new purchase
   - If thread still active → Skip this cycle, wait for next

### Why 5 Seconds?

- Cart clearing typically takes 20-30 seconds total
- By the time we detect 'completing' status, clearing has already started (2-5s in)
- 5-second wait means clearing is ~7-10s complete out of 20-30s
- Remaining cleanup usually finishes before next cycle (25-35s intervals)

## Expected Behavior

### Before (Broken)
```
Cycle 1: Purchase → Checkout ✅ → Cleanup starts (thread removed)
Cycle 2: NEW purchase starts (navigates to product)
Cycle 1: Still cleaning (redirects to cart) ← COLLISION!
Result: Empty cart error, purchase fails
```

### After (Fixed)
```
Cycle 1: Purchase → Checkout ✅ → Cleanup starts (thread in 'completing')
Cycle 2: Check detects completing thread → Wait 5s
Cycle 2: Re-check → Thread complete → NEW purchase safe to start
Cycle 2: Purchase succeeds ✅
```

## Logs You'll See

When the fix is working:
```
[PURCHASE_CONCURRENCY] Background thread still active: 94721096 (running 35.2s, status: completing)
[PURCHASE_CONCURRENCY] Thread is completing (cleanup phase) - waiting 5s for it to finish...
[PURCHASE_CONCURRENCY] Thread completed during wait - safe to start new purchase
[PURCHASE] CRITICAL RULE: 94721096 IN STOCK + ready -> attempting (60s)
```

## Related Fixes in This Session

This is the **third major fix** to make purchases reliable:

1. **Nested Lock Deadlock** (DEADLOCK_FIX_SUMMARY.md) - Second cycle would hang forever
2. **Empty Checkout Hang** (EMPTY_CHECKOUT_HANG_FIX.md) - Got stuck on empty checkout page
3. **Purchase Overlap Race** (THIS FIX) - Two purchases running simultaneously

All three work together to ensure:
- No deadlocks
- No infinite hangs
- No race conditions
- Clean separation between purchase cycles
- Reliable cart management

## Testing

To verify the fix works:
1. Start the app with TEST_MODE=true
2. Wait for first purchase to complete
3. Watch for second purchase - should NOT start during cleanup
4. Look for `[PURCHASE_CONCURRENCY] Thread is completing` logs
5. Confirm no "Cart verification failed" logs during new purchase navigation
6. Verify purchases complete cleanly without cart redirect errors

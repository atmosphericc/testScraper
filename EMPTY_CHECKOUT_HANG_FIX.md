# Fix: Empty Checkout Page Hang

## Problem

After the first successful purchase cycle, the second attempt would:
1. Click add-to-cart ✅
2. Target.com auto-redirects to `/checkout`
3. Checkout page is EMPTY (cart not populated)
4. Code hangs indefinitely on the empty checkout page
5. User forced to press Ctrl+C to stop the program

## Root Cause

**File**: `src/session/purchase_executor.py` line 356

After clicking add-to-cart, the code:
1. Waits 3 seconds for Target's cart API to complete
2. **Doesn't check if page navigated unexpectedly**
3. Calls `await self.session_manager.save_session_state()` which hangs if page is in broken state
4. Never detects that checkout is empty/broken

### Why Target Auto-Redirects

Target.com sometimes automatically navigates to `/checkout` after add-to-cart is clicked, but if:
- The cart API fails or times out
- The item wasn't actually added to cart
- There's a session issue

The result is an **empty checkout page** that never loads properly, causing the code to hang.

## The Fix

**File**: `src/session/purchase_executor.py` lines 353-402

Added three layers of protection:

### 1. Detect Unexpected Navigation (lines 353-357)
```python
current_url = page.url
print(f"[PURCHASE_EXECUTOR] [REDIRECT_CHECK] Current URL after add-to-cart: {current_url}")

if 'checkout' in current_url.lower():
    print(f"[PURCHASE_EXECUTOR] [REDIRECT_DETECTED] ⚠️ Target auto-redirected to checkout!")
```

After clicking add-to-cart, check if Target auto-redirected to checkout.

### 2. Validate Checkout Page (lines 361-387)
```python
# Check if we got redirected back to cart (empty cart scenario)
post_wait_url = page.url
if 'cart' in post_wait_url.lower() and 'checkout' not in post_wait_url.lower():
    raise Exception("Add-to-cart failed - redirected to empty cart")

# Check for empty checkout page
page_content = await page.content()
if 'your cart is empty' in page_content.lower() or 'no items' in page_content.lower():
    raise Exception("Add-to-cart failed - checkout page is empty")
```

If on checkout, verify it's not empty. If empty, **immediately fail** instead of hanging.

### 3. Timeout Protection on Session Save (lines 392-402)
```python
try:
    await asyncio.wait_for(
        self.session_manager.save_session_state(),
        timeout=10.0
    )
    print(f"[PURCHASE_EXECUTOR] [OK] Session state saved after add-to-cart")
except asyncio.TimeoutError:
    print(f"[PURCHASE_EXECUTOR] [WARNING] Session save timed out (non-fatal)")
```

Wrap session save in 10-second timeout. If it hangs, log warning and continue (non-fatal).

## What This Fixes

### Before (Broken)
1. Click add-to-cart
2. Target redirects to empty checkout
3. Code hangs at `save_session_state()`
4. **Infinite hang** - user must force quit

### After (Fixed)
1. Click add-to-cart
2. Target redirects to empty checkout
3. Code detects empty checkout page
4. **Immediately fails** with clear error message
5. Purchase marked as failed
6. Next cycle can retry

## Expected Behavior

When this scenario occurs, you'll see:
```
[PURCHASE_EXECUTOR] [REDIRECT_DETECTED] ⚠️ Target auto-redirected to checkout!
[PURCHASE_EXECUTOR] [REDIRECT_DETECTED] Checking if checkout is empty/broken...
[PURCHASE_EXECUTOR] [REDIRECT_ERROR] ❌ Empty checkout page detected!
[PURCHASE_EXECUTOR] [REDIRECT_ERROR] Add-to-cart failed - checkout page is empty
```

The purchase will be marked as **failed** instead of hanging, and the system will:
- Clear the cart
- Return to homepage
- Wait for next cycle
- Retry the purchase on the next stock check

## Additional Protection

The session save timeout means that even if an unexpected hang occurs elsewhere, the code will recover within 10 seconds instead of hanging indefinitely.

## Testing

To verify the fix:
1. Start the app
2. Wait for a purchase attempt
3. Watch for `[REDIRECT_CHECK]` and `[REDIRECT_DETECTED]` logs
4. Confirm no infinite hangs occur
5. Verify failed purchases are detected and retried on next cycle

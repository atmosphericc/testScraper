# Issue 1: Real Order Number Not Captured — Fix Required in `purchase_executor.py`

## Status
**BLOCKED on off-limits file**: `src/session/purchase_executor.py` — requires owner modification

## Current Problem
When a purchase completes, the real order UUID (e.g., `ad9207a1-1c86-11f1-b49a-0b675f1e8af4`) is visible in:
- The confirmation page URL: `https://www.target.com/order-confirmation?orderId={uuid}`
- The log statement at line 89 of purchase logs: `[PAYMENT] Reached confirmation page: https://www.target.com/order-confirmation?orderId=ad9207a1-1c86-11f1-b49a-0b675f1e8af4`

However, the **return value from `PurchaseExecutor.execute()` does NOT include this order ID**, so:
1. `bulletproof_purchase_manager.py` cannot extract it
2. `purchase_states.json` is written with `"order_number": null`
3. The activity log shows fake/missing order IDs like `Order: None` or `Order: REAL-736932`

## What Needs to Be Fixed

**File**: `src/session/purchase_executor.py`
**Location**: The success return statement around line 1095–1100

**Current code**:
```python
return {
    'success': True,
    'tcin': tcin,
    'reason': 'order_confirmed',
    'execution_time': execution_time
}
```

**Required change** — add two new keys to the return dict:
```python
return {
    'success': True,
    'tcin': tcin,
    'reason': 'order_confirmed',
    'execution_time': execution_time,
    'order_id': orderId,  # ← ADD THIS: extract from tab.url
    'confirmation_url': tab.url  # ← ADD THIS: the full confirmation URL
}
```

## How to Extract the Order ID

At the point where this return statement executes (line 1095), the code has just confirmed the order and the `tab` object is at the confirmation page.

**The order ID can be extracted one of two ways:**

### Option A: Parse from URL (recommended, simpler)
```python
# Before the return statement, around line 1088
import urllib.parse
parsed_url = urllib.parse.urlparse(tab.url)
query_params = urllib.parse.parse_qs(parsed_url.query)
orderId = query_params.get('orderId', [None])[0]

# Then in the return dict:
return {
    'success': True,
    'tcin': tcin,
    'reason': 'order_confirmed',
    'execution_time': execution_time,
    'order_id': orderId,
    'confirmation_url': tab.url
}
```

### Option B: Extract via JavaScript (if URL parsing fails)
```python
# If URL parsing above yields None, fall back to:
try:
    orderId = await tab.evaluate("""
        new URLSearchParams(window.location.search).get('orderId')
    """)
except Exception:
    orderId = None

return {
    'success': True,
    'tcin': tcin,
    'reason': 'order_confirmed',
    'execution_time': execution_time,
    'order_id': orderId,
    'confirmation_url': tab.url
}
```

## Downstream Consumer (Already Ready)

Once this fix is applied, `bulletproof_purchase_manager.py` (lines 1149–1156) will automatically:
1. Read `result['order_id']` from the executor's return dict
2. Write it to `purchase_states.json` under `"order_number"`
3. Pass it to the status callback for UI display
4. No changes needed in bulletproof_purchase_manager.py

## Verification

After the fix is deployed:
1. Run a test purchase
2. Check `logs/purchases/purchase_{tcin}_{timestamp}.log` for the confirmation page URL
3. Check `logs/purchase_states.json` for the matching TCIN entry — should have `"order_number": "{uuid}"` (not null)
4. Check the activity log in the UI — should show the real order ID (not fake, not null)

## Priority
**Low** — does not block purchases; affects data quality and post-purchase verification only.

# Failure-Forensics Agent Guide for Walmart

This guide tells the failure-forensics agent where to look and what to do when Walmart purchases fail.

## Quick Start

**When user says: "Check Walmart logs and fix the issue"**

1. **Read the error log** → `walmart/logs/error_log.txt`
   - Look for `[YYYY-MM-DD HH:MM:SS] [CATEGORY] message` entries
   - Stack traces will be present if an exception was thrown

2. **Check purchase states** → `walmart/logs/purchase_states.json`
   - Look for items with `"status": "failure"` or `"status": "attempting"`
   - Check `last_error` field for the reason
   - Check `final_outcome` for the failure category

3. **Read the per-purchase log** → `walmart/logs/purchases/purchase_*.log`
   - List all logs: `ls -la walmart/logs/purchases/`
   - Read the most recent one to see step-by-step trace
   - Find the exact line where execution stopped

4. **Diagnose and fix**
   - Use the failure categories below to identify root cause
   - Apply targeted fix (selector, timeout, logic, etc.)
   - Update the relevant file in `walmart/`

## Log File Locations

| Log | Path | Contents |
|-----|------|----------|
| Errors | `walmart/logs/error_log.txt` | Exceptions with traceback |
| Activity | `walmart/logs/activity_log.pkl` | Timeline of all events |
| States | `walmart/logs/purchase_states.json` | Current status of each item |
| Per-Purchase | `walmart/logs/purchases/purchase_*.log` | Step-by-step trace |

## Failure Categories & Fixes

### 1. WRONG_SELECTOR
**Symptom**: "Could not find element" or selector timeout in per-purchase log

**Diagnosis**:
- Open `walmart/logs/purchases/purchase_*.log` and find the timeout message
- Example: `[PURCHASE] Timeout waiting for [data-automation-id="place-order-btn"]`

**Fix**:
- Update the selector list in the appropriate file
- **ATC button**: `walmart/purchase_executor.py:45-52` (`ATC_SELECTORS`)
- **Checkout button**: `walmart/purchase_executor.py:55-60` (`CHECKOUT_SELECTORS`)
- **Place Order**: `walmart/purchase_executor.py:62-67` (`PLACE_ORDER_SELECTORS`)
- **CVV input**: `walmart/purchase_executor.py:69-76` (`CVV_SELECTORS`)
- Add new selector at the beginning of the list for highest priority

**Example Fix**:
```python
# Find this in walmart/purchase_executor.py line 62:
PLACE_ORDER_SELECTORS = [
    'button[data-automation-id="place-order-btn"]',
    'button:has-text("Place order")',
    ...
]

# If button has new attribute, add it:
PLACE_ORDER_SELECTORS = [
    'button[data-testid="checkout-final-btn"]',  # NEW
    'button[data-automation-id="place-order-btn"]',
    'button:has-text("Place order")',
    ...
]
```

### 2. ANTIBOT_BLOCK
**Symptom**: "403 Pardon Our Interruption" or `/blocked` redirect in logs

**Diagnosis**:
- Read error log: look for `[purchase_executor] ... Akamai|PerimeterX` errors
- Read per-purchase log: look for `/blocked` or HTTP 403 responses

**Fix**:
- **No code fix needed** — this is a session/proxy issue
- Recommendation: rotate proxy IP
- Log: Add a note to `walmart/logs/patches.log` explaining the block
- Example fix response:
  ```
  [2026-04-08] Akamai 403 block detected for item 15042474261
  Fix: Recommend proxy rotation (WalmartProxyManager.rotate())
  No code changes needed.
  ```

### 3. QUEUE_TIMEOUT
**Symptom**: "Queue timeout" in `purchase_states.json` with `final_outcome: "timeout"`

**Diagnosis**:
- Check `purchase_states.json` → `last_error: "Queue timeout"`
- Read per-purchase log → look for "waiting for pass-through" message

**Fix**:
- **No code fix needed** — queue was never released
- Recommendation: Retry on next stock signal
- Log entry in `walmart/logs/patches.log`:
  ```
  [2026-04-08] Queue timeout for item 15042474261
  Fix: Normal - Walmart released other users first. Will retry on next in-stock.
  ```

### 4. OUT_OF_STOCK (OOS)
**Symptom**: "Item not found in cart after ATC" or OOS error in checkout

**Diagnosis**:
- Read per-purchase log → look for cart verification failure
- Check `purchase_states.json` → `final_outcome: "cart_verify_failed"`

**Fix**:
- **No code fix needed** — item sold out during purchase
- Log: Item was in stock but sold out before checkout completed
- **Note for future**: This is a race condition; try faster ATC if possible

### 5. NETWORK_TIMEOUT
**Symptom**: "Timeout waiting for [element]" or navigation timeout in logs

**Diagnosis**:
- Read per-purchase log → look for timeout on navigation/element wait
- Example: `[NAVIGATE] Timeout waiting for product page to load`

**Fix**:
- Increase timeout constants in `walmart/config.py` or `purchase_executor.py`
- Find the timeout constant used and increase it
- Example: `NAVIGATE_TIMEOUT = 30000` → `NAVIGATE_TIMEOUT = 45000` (ms)

**Files to check**:
- `walmart/purchase_executor.py:42` → `NAVIGATE_TIMEOUT`
- `walmart/config.py` → various timeout constants
- Find exact line in per-purchase log, then locate the corresponding timeout in code

### 6. LOGIN_FAILURE
**Symptom**: "Login failed" or "Could not authenticate" in error log

**Diagnosis**:
- Read error log for login-related errors
- Check per-purchase log for auth failures

**Fix**:
- **Needs human intervention** — cannot auto-fix login issues
- Log: Stop the bot and notify user to re-login
- Example:
  ```
  [2026-04-08] Login failure for Walmart session
  Fix: MANUAL - User must re-run walmart_relogin.py to refresh credentials
  No automatic recovery available.
  ```

### 7. UNKNOWN / GENERIC_ERROR
**Symptom**: "Unexpected error" or exception with unknown cause in logs

**Diagnosis**:
- Read error log for full exception traceback
- Read per-purchase log to see where execution stopped
- Look for unusual error messages

**Fix**:
- Analyze the exception and traceback
- Determine the root cause
- Apply appropriate fix based on diagnosis
- If diagnosis is unclear, create a patch log entry documenting the uncertainty

**Example**:
```
[2026-04-08] Unknown error: IndexError in _confirm_shipping
Diagnosis: Unexpected HTML structure for shipping options
Fix: Added fallback selector for shipping option detection
Changes: walmart/purchase_executor.py lines 580-590
```

## How to Update Log Files

### After Fixing Code
1. Update `walmart/LOGGING.md` with what was fixed
2. Add entry to patch log (created by self-healing agent):
   ```
   [2026-04-08 14:35:22] Category: WRONG_SELECTOR
   Item: 15042474261
   Fix: Updated PLACE_ORDER_SELECTORS in purchase_executor.py
   New selector: button[data-testid="checkout-final"]
   Status: PATCHED, restart initiated
   ```

3. Log state changes:
   ```python
   walmart_logger.log_purchase_state(item_id, {
       'status': 'ready',  # Reset to ready for retry
       'timestamp': datetime.now().isoformat(),
       'attempt_count': 2,
       'final_outcome': None,  # Clear previous failure
       'last_error': 'Fixed - retrying',
       'order_id': None
   })
   ```

### Creating Patch Log Entry
When the self-healing agent makes a fix, it should write to `walmart/logs/patches.log`:

```python
from datetime import datetime

patch_log = {
    'timestamp': datetime.now().isoformat(),
    'category': 'WRONG_SELECTOR',
    'item_id': '15042474261',
    'failure_reason': 'Could not find Place Order button',
    'fix_applied': 'Added new selector: button[data-testid="checkout-final"]',
    'files_modified': ['walmart/purchase_executor.py'],
    'status': 'PATCHED'
}

# Append to patch log file
with open('walmart/logs/patches.log', 'a') as f:
    json.dump(patch_log, f)
    f.write('\n')
```

## Reading Per-Purchase Logs

### Finding the Right Log File
```bash
# List all purchase logs (newest first)
ls -lt walmart/logs/purchases/purchase_*.log | head -5

# Read the most recent one
cat walmart/logs/purchases/purchase_15042474261_20260408_143215.log
```

### Key Lines to Look For
```
[PURCHASE] Starting purchase attempt for {item_id}
[NAVIGATE] Loaded {url}
[ATC] Clicked button
[ATC] Confirmed in cart
[CHECKOUT] Navigating to checkout
[CONFIRM_SHIPPING] Confirming address
[CVV] Entering CVV
[PLACE_ORDER] Clicking Place Order button
[PURCHASE] ORDER PLACED! ID: {order_id}
[PURCHASE] Error: {error_message}
```

## Decision Tree for Failure Agent

```
Read error_log.txt
    ↓
Did it find an exception?
    ├─ YES → Read full traceback
    │         ↓
    │         Is it a timeout?
    │         ├─ YES → NETWORK_TIMEOUT (increase timeout)
    │         ├─ NO → Go to next step
    │
    └─ NO → Check purchase_states.json
            ↓
            What is final_outcome?
            ├─ "timeout" → QUEUE_TIMEOUT (no fix, retry)
            ├─ "cart_verify_failed" → OUT_OF_STOCK (no fix, lost race)
            ├─ "atc_failed" → Read per-purchase log
            │                 → Check for selector errors
            │                 → Might be WRONG_SELECTOR
            ├─ "checkout_nav_failed" → NETWORK_TIMEOUT or blocked page
            ├─ "error" → Read full traceback in error_log
            └─ "antibot" → ANTIBOT_BLOCK (rotate proxy)

Read per-purchase log (purchase_*.log)
    ↓
Find the last successful step before failure
    ↓
    ├─ Timeout on selector → WRONG_SELECTOR (update selector list)
    ├─ "/blocked" or 403 → ANTIBOT_BLOCK (no code fix)
    ├─ Navigation timeout → NETWORK_TIMEOUT (increase timeout)
    ├─ Login error → LOGIN_FAILURE (needs human intervention)
    └─ Other error → UNKNOWN (analyze and determine fix)
```

## Common Issues & Quick Fixes

| Issue | Log Entry | Fix |
|-------|-----------|-----|
| "Could not find Place Order button" | `Timeout [data-automation-id="place-order-btn"]` | Add new selector to `PLACE_ORDER_SELECTORS` |
| "timeout waiting for cart to load" | `Timeout loading walmart.com/cart` | Increase `NAVIGATE_TIMEOUT` in `purchase_executor.py` |
| "CVV modal never appeared" | `Timeout waiting for CVV input` | Add new selector to `CVV_SELECTORS` or increase timeout |
| "Blocked by Akamai" | `403 Pardon Our Interruption` | Rotate proxy (code fix not possible) |
| "Queue never released" | `Queue timeout after 300s` | Retry on next in-stock (normal) |
| "Item already removed from cart" | `Item not found in cart` | Item OOS, retry on next restock |

## Testing Your Fix

After making a code change, before marking the failure as resolved:

```bash
# 1. Verify syntax
python -m py_compile walmart/purchase_executor.py
python -m py_compile walmart/config.py

# 2. Check logs directory exists
mkdir -p walmart/logs/purchases

# 3. Look for success pattern in logs
grep -i "order placed\|purchased" walmart/logs/activity_log.pkl

# 4. Verify no new errors were introduced
tail -20 walmart/logs/error_log.txt
```

## Calling the Failure Agent

From the main conversation, tell the failure agent:

```
User: "Walmart purchase failed. Check walmart/logs/ and fix it."

Failure Agent will:
1. Read walmart/logs/error_log.txt
2. Check walmart/logs/purchase_states.json
3. Find most recent walmart/logs/purchases/purchase_*.log
4. Diagnose root cause using decision tree
5. Apply fix to appropriate walmart/ file
6. Update walmart/LOGGING.md with what was fixed
7. Return diagnosis and fix summary
```

## Summary

When Walmart fails:
1. **Error log** → `walmart/logs/error_log.txt` (exceptions)
2. **State file** → `walmart/logs/purchase_states.json` (what failed)
3. **Per-purchase log** → `walmart/logs/purchases/purchase_*.log` (why it failed)
4. **Fix** → Update appropriate walmart/ file and test
5. **Document** → Update walmart/LOGGING.md with what was fixed

The failure-forensics agent now has full visibility into Walmart operations.

# Failure Log

## Format
### [DATE] - Failure Type - Retailer
**Symptom**:
**Root Cause**:
**Fix Applied**:
**Confidence**: high/medium/low
**Outcome**:

---
Entries added by @failure-forensics

### [2026-04-08] - Shutdown Race Condition - Target
**Symptom**: TCIN stuck in "attempting" with final_outcome "unknown" after app kill during active purchase
**Root Cause**: shutdown_handler called os.kill(SIGKILL) on Chrome process without writing terminal state for in-progress purchase thread
**Fix Applied**: shutdown_handler now iterates active purchases and writes "interrupted" state before killing browser
**Confidence**: high
**Outcome**: Prevents double-purchase on restart; stuck state now resolves immediately instead of waiting for 60s auto-reset

### [2026-04-08] - Stuck Attempting State (Manual Recovery) - Target
**Symptom**: TCIN 95225596 ("2025 Panini NFL Select Football Trading Card Blaster Box") remained in `"status": "attempting"` / `"final_outcome": "unknown"` in `logs/purchase_states.json`. Blocked all future purchase attempts for that TCIN.
**Root Cause**: First purchase run (purchase_95225596_20260406_124606.log) succeeded fully — order `ad9207a1-1c86-11f1-b49a-0b675f1e8af4` confirmed at line 89 of that log. Nine seconds later the stock monitor re-triggered a second purchase attempt; shutdown fired at 12:46:20 while the second run was at the Place Order step (`[CLEANUP] os.kill(80081, SIGKILL)`). The existing shutdown_handler fix had not yet written "interrupted" state before the kill, leaving the state file with the second run's "attempting" entry.
**Fix Applied**: Manually reset `logs/purchase_states.json` entry for `95225596` from `{"status": "attempting", ...}` to `{"status": "ready"}`.
**Confidence**: high
**Outcome**: TCIN 95225596 will be purchasable again on next in-stock detection. Note: first run confirmed real purchase order `ad9207a1-1c86-11f1-b49a-0b675f1e8af4`.

### [2026-04-08] - Fake Order Number Logged - Target
**Symptom**: Activity log showed `Order: None` then `Order: REAL-736932` for a confirmed purchase. Real order ID `ad9207a1-1c86-11f1-b49a-0b675f1e8af4` was visible in the confirmation URL logged by `purchase_executor.py` but never captured.
**Root Cause**: `src/session/purchase_executor.py` (off-limits) returns `{'success': True, 'tcin': ..., 'reason': 'order_confirmed', 'execution_time': ...}` with no `order_id` key. `_update_purchase_result` in `bulletproof_purchase_manager.py` fell through to `f"REAL-{random.randint(...)}"` fake fallback (line 1152). Additionally, `app.py` logged both successful and failed real purchases with a `"MOCK:"` prefix, which was a copy-paste artifact from test mode.
**Fix Applied**:
1. `src/purchasing/bulletproof_purchase_manager.py` line 1152: replaced fake `REAL-{random}` fallback with explicit `None` + warning log. Added `confirmation_url` parsing path (splits `?orderId=` param) so the fix is ready once executor returns `confirmation_url` in its result dict.
2. `app.py` lines 715-717: removed `"MOCK:"` prefix from purchased/failed activity log messages; added `or 'unknown'` guard so `None` order number is surfaced explicitly rather than silently.
**Root Fix Still Needed**: `src/session/purchase_executor.py` must be updated to include `'order_id': orderId_from_url` and optionally `'confirmation_url': tab.url` in its success return dict at the `_complete_checkout` return site (~line 1089). The order ID is available in `tab.url` at the moment `[PAYMENT] Reached confirmation page` is logged.
**Confidence**: high (diagnosis); medium (full fix pending executor change)
**Outcome**: No more fake order IDs written to state file. Warning log will fire on next purchase until executor is patched to return order_id.

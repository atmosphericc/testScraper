# Walmart Bot Deployment Notes - 2026-04-29

## Issue Fixed
Delivery modal ("What day works best for you?") was causing infinite click loops during checkout, leading to Akamai/PerimeterX bot detection. Stock monitor would accumulate 1000+ errors after each purchase.

## Solution Applied
Three commits with comprehensive fixes:
- `c6ab183a` - Add flyout diagnostics and post-purchase session re-warm
- `5c2247e5` - Prevent infinite delivery day modal loop during checkout
- `3fe9afee` - Documentation of fixes

## Important: Fresh Deployment Required

**When deploying to production, you MUST do a complete clean restart:**

```bash
# 1. Kill all processes and clear ports
python clearPort.py
pkill -9 Chrome
pkill -9 "Google Chrome"
sleep 3

# 2. Clear all Python caches (CRITICAL!)
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null
find . -name "*.pyo" -delete 2>/dev/null

# 3. Start fresh
python -m walmart.walmart_app
```

## Why Cache Clearing is Critical

Python caches compiled bytecode in `.pyc` files. If you update code but don't clear cache:
- Old code continues running from cached `.pyc` files
- Your changes are invisible to the running process
- This happened during testing and caused confusion

**Always clear cache before deployment of code changes.**

## Test Results (Fresh Deployment)

```
✅ Checkout flow completes cleanly
✅ No infinite modal loops (was 10+ repeats, now 0-1)
✅ Delivery modal detected and handled without blocking
✅ Session re-warm successful after purchase
✅ Stock monitor: 1.5/s | 0 errors (sustained)
✅ No bot detection after checkout
```

## Files Modified

1. **walmart/purchase_executor.py**
   - `_try_direct_checkout_from_flyout()` - Added "Attempting fast path" logging
   - `_handle_delivery_day_modal()` - Modal visibility check, closure verification, error handling
   - `_confirm_shipping()` - Modal handling loop prevention with MAX_MODAL_HANDLES = 3
   - `_clear_cart()` - Session re-warm call after each purchase

2. **walmart/session_manager.py**
   - `rewarm_tab1()` - Changed from full navigation to cookie snapshot (no detection risk)

3. **walmart/walmart_app.py**
   - Enhanced logging setup with file handlers

## Known Limitations

1. **ATC Flyout**: Still doesn't appear in current Walmart A/B tests
   - Fast path checkout would skip cart page if flyout appears
   - Falls back gracefully to cart page (no impact on functionality)

2. **Modal Selectors**: If Walmart changes modal structure, selectors may need updating
   - Check logs for "No delivery day modal detected" vs "modal handled"
   - If modal stops working, review FAILURES.md for selector patterns

## Monitoring

After deployment, watch for:
- ✅ "No delivery day modal detected — proceeding" (good - no modal)
- ✅ "Delivery day modal handled (1/3)" (good - modal dismissed on first try)
- ⚠️ "Delivery day modal handled (2/3)" or "(3/3)" (modal appearing repeatedly, may need selectors updated)
- ❌ Monitor errors > 100 after purchase (indicates session stale, check re-warm)

## Rollback

If issues occur:
```bash
git revert 3fe9afee  # Revert docs
git revert 5c2247e5  # Revert modal fix
git revert c6ab183a  # Revert flyout diagnostics
python clearPort.py && python -m walmart.walmart_app
```

## Contact

If deployment issues occur:
1. Check cache is cleared (find . -name "*.pyc" -delete)
2. Check Chrome processes killed (ps aux | grep Chrome)
3. Check ports cleared (lsof -i :5000-5003)
4. Review latest logs for error messages
5. Check git log for recent changes

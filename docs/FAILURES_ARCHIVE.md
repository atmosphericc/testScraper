# Failure Archive

Historical failure entries archived from `docs/FAILURES.md` on 2026-05-14 after
the Round 2 resilient-stack pivot. Most entries are either >30 days old or
Walmart-specific (and Walmart is no longer the active focus — see
`docs/RESILIENT_STACK.md` for the current architecture).

---

### [2026-05-11 PM7] - Cross-Cutting Concerns: 5 Fixes - Walmart
**Symptom**: Post-PM6 audit identified five issues that span multiple files: session-restart hygiene, deprecated `asyncio.get_event_loop()`, unguarded read-modify-write of GraphQL hash globals from CDP threads, unbounded activity-log growth.
**Root Cause / Fix Applied**:
1. `session_manager.stop()` left restart-tainted state. Extended to call `stop_harvester()` early; under their respective locks clear `_pending_graphql` + `_pending_graphql_tab2`, reset `_live_cookies` / `_px3_timestamp` / `_monitor_cookie_snapshot`, and `_last_validation` / `_last_validation_result`.
2. Harvester was never restarted after session restart. `purchase_manager._run_purchase` session-restart branch now re-invokes `start_harvester` post-validate.
3. `asyncio.get_event_loop()` switched to `get_running_loop()` with `RuntimeError` fallback in `purchase_manager.start()`.
4. `config.set_graphql_hash_atf/btf` thread-safety: added module-level `_graphql_hash_lock` guarding the read-modify-write of the module globals.
5. `WalmartLogger._activity_log` bounded via `ACTIVITY_LOG_MAX_ENTRIES = 2000`.

Files: `walmart/session_manager.py`, `walmart/purchase_manager.py`, `walmart/config.py`, `walmart/logging_manager.py`. **Confidence**: high (1-3, 5); medium-high (4).

### [2026-05-11 PM6] - Deep Audit of 4 Largest Walmart Files: 9 Fixes - Walmart
**Symptom**: Pre-run deep audit of `queue_handler.py`, `purchase_manager.py`, `session_manager.py`, `purchase_executor.py` surfaced an outright NameError, a silent cookie-domain-mismatch bug, a session-state cache that masked logout failures, a state-machine bug that permanently stuck items in PURCHASING, and several missing `asyncio.wait_for` wrappers.
**Fix Applied**:
1. `queue_handler.py:enter_queue` wrapped in `asyncio.wait_for(..., timeout=15.0)`. Dropped dead `:has-text(...)` patterns. Removed unreachable `return False`.
2. `purchase_manager.py:_on_stock_change` adds `_lock`-guarded `_in_stock_ids.discard()` on OOS branch.
3. `purchase_manager.py:_on_in_stock_signal` priority-override stashes `original_item_id` and resets it back to MONITORING if overridden.
4. `purchase_manager.py:_is_monitor_healthy` uses public `is_healthy()`. `stop()` got idempotency guard.
5. `session_manager.py`: added `import os` (line 971 used `os.environ.get` without import).
6. `validate_session` caches False outcomes too via `_last_validation_result`.
7. `save_cookies` switched to `cdp.network.get_all_cookies()` + atomic tempfile write.
8. Tab 2 GraphQL handler asymmetry fixed: separate `_pending_graphql_tab2` dict + `_on_loading_finished_tab2` handler; `_fetch_graphql_body` takes a `page` parameter.
9. `purchase_executor.py`: 6 remaining navigations wrapped in `asyncio.wait_for` with 15-20s timeouts.

Files: `walmart/queue_handler.py`, `walmart/purchase_manager.py`, `walmart/session_manager.py`, `walmart/purchase_executor.py`. **Confidence**: high.

### [2026-05-11 PM5] - Remaining Walmart Files Audit: 11 Fixes - Walmart
**Symptom**: Pre-run sweep of `stock_check.py`, `proxy_manager.py`, `blueprint.py`, `self_healing_agent.py`.
**Fix Applied**:
1. `stock_check.py:184`: replaced `parse_cookies(COOKIES_RAW)` (undefined) with `load_cookies()`.
2. `proxy_manager.py:_ProxyStats`: internal `_lock` for `record_success/record_error/error_rate`.
3. `proxy_manager.py:_stats` accessor `_get_stats(proxy)` under `_stats_lock`.
4. `proxy_manager.py:release_checkout_proxy` inverted gate detects double-release with WARN.
5. `blueprint.py`: `stop_manager(timeout=5.0)` exposed + auto-registered via `atexit`. Status routes wrapped in try/except.
6. `self_healing_agent.py:_RULES` WRONG_SELECTOR regex tightened to specific phrases (no more matching "Page not found 404").
7. `self_healing_agent.py:_capture_page_html` now calls `session.get_checkout_page()` (Tab 2) first.
8. `self_healing_agent.py`: `MAX_RESTARTS_PER_HOUR = 8` rolling-window breaker.
9. `self_healing_agent.py:HtmlSelectorPatcher._FILE_MAP` now `dict[str, list[str]]` — ATC patches both files.

Files: `walmart/stock_check.py`, `walmart/proxy_manager.py`, `walmart/blueprint.py`, `walmart/self_healing_agent.py`. **Confidence**: high.

### [2026-05-11 PM4] - stock_monitor.py Concurrency Audit: 6 Latent Races - Walmart
**Symptom**: Pre-run audit of `walmart/stock_monitor.py` (NUM_DISPATCHERS=3 concurrent threads sharing mutable state) surfaced six races and a session-teardown crash path.
**Fix Applied**: `_cb_lock` guarding consecutive-blocked + circuit-breaker state; cooldown finalizer rewritten with witness pattern + 0-1.5s jitter; `_rate_limit_lock` for rate-limit hits/backoff; rate-limit counter resets at backoff window end; `_trigger_graphql_hash_refresh` 30s dedupe; `_run_browser_fetch` / `_log_px3_state_on_block` snapshot `_page` + `_event_loop` before use.

Files: `walmart/stock_monitor.py`. **Confidence**: high (1-5), medium-high (6).

### [2026-05-11] - Standalone Dashboard Audit: 4 Latent Bugs - Walmart
**Fix Applied**:
1. `CHECKOUT_MODE` default → `"PRODUCTION"` (was silently TEST-mode in LIVE badge).
2. `/reorder-products` Flask route added.
3. `FileHandler` attached to `walmart` package logger so submodules write to log file.
4. `queue_handler.py:ATC_SELECTORS` mirrors `purchase_executor.ATC_SELECTORS` (primary `data-automation-id="atc"`).

Files: `walmart/walmart_app.py`, `walmart/queue_handler.py`. **Confidence**: high.

### [2026-05-11 PM] - Audit Follow-Ups: 3 Open Gaps Closed + blueprint.py Parity - Walmart
**Fix Applied**:
1. All three cart navigations wrapped in `asyncio.wait_for(timeout=15.0)`. On timeout, `_clear_cart` sets `_last_cart_clear_blocked = True`.
2. Cookie harvester got `pause_harvester()` / `resume_harvester()`; `_run_purchase` pauses it during checkout.
3. `blueprint.py:35` `CHECKOUT_MODE` default flipped `"LIVE"` → `"PRODUCTION"`.

Files: `walmart/purchase_executor.py`, `walmart/session_manager.py`, `walmart/purchase_manager.py`, `walmart/blueprint.py`. **Confidence**: high.

### [2026-05-11 PM2] - walmart_app.py Round 2: Lifecycle + Robustness - Walmart
**Symptom**: Six 0-byte `logs/walmart_app_*.log` files from 10:06-10:15 — Ctrl+C killed daemon thread before any logger output.
**Fix Applied**:
1. `_graceful_shutdown(signum, frame)` SIGINT/SIGTERM handler. Schedules `stop()` on manager loop (5s budget). Double-Ctrl+C escape via `os._exit(1)`.
2. `FileHandler(log_file, delay=True)` — file only created on first write.
3. `/api/status` and `/health` wrap `_manager.get_status()` in try/except.

Files: `walmart/walmart_app.py`. **Confidence**: high.

### [2026-05-11 PM3] - State File Durability: Atomic Writes + Crash-Safe Loaders - Walmart
**Symptom**: `walmart_config.json`, `purchase_states.json`, `activity_log.pkl` all written non-atomically. SIGKILL during write window left files in a half-written state.
**Fix Applied**:
1. `walmart/config.py`: `_atomic_write_json(p, data)` helper — tempfile + fsync + `os.replace`.
2. `logging_manager.py:log_purchase_state`: same pattern for `purchase_states.json`.
3. `logging_manager.py:_save_activity_log`: same pattern for pickle.
4. `get_walmart_logger()`: double-checked locking under `_walmart_logger_lock`.
5. `get_config()`: catches `JSONDecodeError` / `OSError` and returns `{"products": []}`.

Files: `walmart/config.py`, `walmart/logging_manager.py`. **Confidence**: high.

### [2026-04-29] - Blocked After Checkout: "What Day to Pick Up" Modal Not Dismissed - Walmart
**Symptom**: Bot successfully reaches checkout, clicks Continue buttons through shipping/payment steps, but gets blocked by Akamai/PerimeterX while a "What day to pick up?" modal is still visible.
**Root Cause**: Modal appears mid-step (after "Deliver here" click) but was only handled at the very end of purchase flow. Modal blocks selector discovery → retries → rate-limit escalation. Weak selector coverage.
**Fix Applied**:
1. Expanded `_handle_delivery_day_modal()` selectors (data-automation-id variants, text, role-based, generic dialog fallback). Returns True/False.
2. Modal check integrated into `_confirm_shipping()` loop on every iteration.
3. `_select_delivery_option()` now waits 500-1000ms then tries `_handle_delivery_day_modal()`.
4. Added fallback `_dismiss_any_modal()` method.

Files: `walmart/purchase_executor.py`. **Confidence**: high.

### [2026-04-10] - ATC Button Never Found in _wait_for_page_ready — Buybox Lazy-Load + :has-text() Skipped - Walmart
**Symptom**: `_wait_for_page_ready()` logs `next_data_at=0.3s, button_at=never` and hits the full timeout every time.
**Root Cause**: Buybox lazy-load (React reconciler commits buybox 3-10s after `__NEXT_DATA__`). All `:has-text()` selectors unconditionally skipped in `_wait_for_page_ready()`. 8s timeout too short.
**Fix Applied**:
1. `_wait_for_page_ready()`: `:has-text()` skip guard replaced with XPath conversion.
2. `_navigate()`: timeout 8000ms → 13000ms.

Files: `walmart/purchase_executor.py`. **Confidence**: high.

### [2026-04-10] - ATC Not Clicked on Tab 2 (Checkout Tab) — React Hydration Not Awaited - Walmart
**Symptom**: Executor navigates to product page on Tab 2 but ATC never found/clicked.
**Root Cause**: `_wait_for_page_ready()` was documented as wired in but the call was never actually inserted. Plus `_add_to_cart` JS evaluate string was missing the `f` prefix.
**Fix Applied**: Added `await self._wait_for_page_ready(timeout=8000)` in `_navigate()`. Changed JS evaluate to `f"""..."""` with escaped `{{` / `}}`.

Files: `walmart/purchase_executor.py`. **Confidence**: high.

### [2026-04-10] - React Hydration Race Condition — ATC Not Clicked on First Visit - Walmart
**Symptom**: ATC button not found/clicked on first or second attempt; cycled empty cart → product 2-3× before ATC finally clicked.
**Root Cause**: Walmart React hydration takes 3-5s; bot was waiting only 1.2-2.8s before searching.
**Fix Applied**: Added `_wait_for_page_ready(timeout=8000)` polling for `window.__NEXT_DATA__` + ATC selector visibility.

Files: `walmart/purchase_executor.py`. **Confidence**: high.

### [2026-04-10] - ATC + Cart + Checkout Multi-Failure - Walmart
**Fix Applied**:
1. `_add_to_cart`: deadline 6s → 12s. Added `_btn_state_changed()` (disabled/aria-disabled/"added" text). Screenshot on no-flyout path.
2. `_verify_cart`: 5s poll loop. Fallback path fails (was false-positive).
3. `_go_to_checkout`: URL check `"checkout" in url` → `"/checkout" in url and "/cart" not in url`.
4. `_select_delivery_option`: timeout 3s → 5s. Stable `data-automation-id` selectors first.

Files: `walmart/purchase_executor.py`. **Confidence**: high.

### [2026-04-08] - Shutdown Race Condition - Target
**Symptom**: TCIN stuck in "attempting" with final_outcome "unknown" after app kill during active purchase.
**Root Cause**: `shutdown_handler` called `os.kill(SIGKILL)` on Chrome without writing terminal state for in-progress thread.
**Fix Applied**: `shutdown_handler` now iterates active purchases and writes "interrupted" state before killing browser.
**Confidence**: high. Prevents double-purchase on restart.

### [2026-04-08] - Stuck Attempting State (Manual Recovery) - Target
**Symptom**: TCIN 95225596 stuck `"status": "attempting"` / `"final_outcome": "unknown"` in `logs/purchase_states.json`.
**Root Cause**: First purchase succeeded (order `ad9207a1-1c86-11f1-b49a-0b675f1e8af4`); 9s later second attempt fired and shutdown left "attempting" entry.
**Fix Applied**: Manually reset `logs/purchase_states.json` entry to `{"status": "ready"}`.

### [2026-04-08] - Fake Order Number Logged - Target
**Symptom**: Activity log showed `Order: REAL-736932` (random fallback) for a confirmed purchase.
**Root Cause**: `purchase_executor.py` returned no `order_id`; `bulletproof_purchase_manager.py:1152` fell through to `f"REAL-{random.randint(...)}"`. `app.py` also logged real purchases with a `"MOCK:"` prefix.
**Fix Applied**:
1. `bulletproof_purchase_manager.py:1152`: replaced fake fallback with explicit `None` + warning. Added `confirmation_url` parsing.
2. `app.py:715-717`: removed `"MOCK:"` prefix.
3. (Subsequent: executor at `src/session/purchase_executor.py:1217-1239` now returns `order_id` + `confirmation_url`.)
**Confidence**: high. No more fake order IDs.

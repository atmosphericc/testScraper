# Resume note — Target API perf (2026-05-07 PM, v17 shipped)

> Use this on the other computer: `cd` into the repo, `git pull origin bugFix_3.0_hybrid_v1`, then point Claude Code at this file.

## Where we left off (v17)

Two commits on `bugFix_3.0_hybrid_v1` (pushed to origin):
- `6efcdd16` — v16 baseline (skip-PDP, skip-checkout-page, 2-attempt fast-retry, stock-monitor pause, deadlock fix, fast-nav)
- `d0f4a87c` — v17 layered perf wins (rotating Shape-cache + warmup re-nav skip + retry-2 jitter trim)

**v17 results (`logs/20260507_test_app_v17.log`, n=23):**
- avg **2.64s** | min 2.01s | p50 2.53s | p90 **3.09s** | max 4.01s
- 0 bails, 100% effective success
- ATC 401 rate 6/23 (~26%, statistically same as v16's 20%)
- **Retry-1 success rate: 6/6 = 100%** (v16 was 7/13 = 54%)
- Retry-2 never fired (ring rotation rescued everything on retry-1)
- Warmup re-nav skipped 7 times (cart_nav_age 6-79s)

**vs v16 (n=64):** avg 3.46s → 2.64s | p90 7.19s → 3.09s | retry-1 success 54% → 100%.

The ring buffer is the dominant win — it doesn't change the 401 rate, it makes recovery ~free.

## What's wired and committed (uncommitted = none)

### Layer 1 — Rotating Shape-cache ring
- `_shape_capture_ring: Deque[Dict]` (maxlen=4) in `PurchaseExecutor.__init__` at `src/session/purchase_executor.py:48-57`.
- Interceptor write site (`src/session/purchase_executor.py:~440`) appends `{headers, ts, consumed: False}` whenever it writes `_cached_cart_headers`.
- `_consume_fresh_capture()` at `src/session/purchase_executor.py:~614` scans newest-first, marks first unconsumed entry consumed, republishes its headers/ts as the active cache view. Returns True/False.
- Called pre-ATC (`_execute_purchase_impl` ~line 891) and after `warm_shape_headers()` refresh (~line 904).
- Retry-2 path (~line 1086) tries `_consume_fresh_capture()` first, only falls back to `warm_shape_headers()` if ring exhausted.

### Layer 2 — Warmup re-nav skip + readyState poll
- `_warmup_tab_cart_ts: float` tracks last successful /cart nav.
- If `cart_nav_age < 90s`, skip the re-nav entirely; fire dummy POST against already-loaded page (~line 651-672).
- Fresh navs replace unconditional 0.8s sleep with `document.readyState=='complete'` poll capped at 0.4s.

### Layer 3 — Retry-2 jitter trim
- Retry-2 sleep tightened from 0.6-1.1s (`0.6 + (time.time() % 0.5)`) to 0.2-0.4s (`0.2 + (time.time() % 0.2)`) at `src/session/purchase_executor.py:~1078`.
- Per-Device-ID bucket refills sub-300ms with a fresh capture; longer human-shaped jitter was over-conservative for an API-only retry.

### Existing v16 stack (still in place)
- Phase 4b API place-order (`TARGET_API_PLACE_ORDER=true`, auto-on in TEST_MODE; compose-and-abort guard in TEST_MODE at `_api_place_order` ~line 3243).
- Phase 4c API cart-clear (`TARGET_API_CART_CLEAR=true`, auto-on in TEST_MODE).
- Skip PDP nav when qty>1 from RedSky or `_pdp_qty_cache` hit (30-min TTL).
- Skip /checkout/start nav when API place-order is on.
- API-only mode bail (no DOM fallback) when not on PDP.
- Stock-monitor `set_suspended()` flipped at purchase start/end.
- Two-attempt fast-retry on ATC 401.
- `_fast_nav` (cdp.page.navigate + readyState=interactive).
- ATC fetch wrapped with 8s timeout; cart-state parsed from ATC body.
- Phase 6 dispatch wired but NOT yet live-tested at N≥2 (needs second account).

## Open architectural decisions (pick one to start)

- **Option D: Run a longer v17 sweep on test_app.py.** Confirm n=23 numbers hold over n=100+. ~5-10 min runtime, no code change.
- **Option E: One more squeeze pass on clean cycles.** Maybe 150-200ms left in the ATC fetch path (e.g. drop the `_setup_cdp_fetch_interceptor` re-run on every cycle if it's not actually changing handlers; trim the `[STATE_CARRY]` log noise). Uncertain whether the floor is set by Target or by our overhead.
- **Option C: Phase 6 multi-account.** Needs a second real Target account + `TARGET_SESSION_PATH=target-2.json TARGET_PROFILE_DIR=nodriver-profile-2 python relogin.py`, then `TARGET_WORKER_POOL_SIZE=2 python app.py`. Eliminates per-Device-ID rate limit by spreading volume across N Device IDs.

## Run command (TEST_MODE on test_app.py)

```bash
python -u test_app.py 2>&1 | tee logs/20260507_test_app_v17.log
```

For PROD (real orders): `python -u app.py`. Same flags. PROD bypasses the compose-and-abort guard and fires real fetches.

## Run history (latest first)

| Run | Cycles | Errors | Avg | p50 | p90 | Bail rate | Notes |
|-----|--------|--------|-----|-----|-----|-----------|-------|
| v17 | 23 | 0 | 2.64s | 2.53s | 3.09s | 0% | Ring rotation + warmup nav skip + retry-2 jitter trim |
| v16 | 64 | 0 | 3.46s | 2.83s | 7.19s | 1.6% | 2-attempt retry; 401 rate 20% |
| v15 | 45 | 0 | 2.95s | — | — | 6.7% | Clean bail path; cross-sell button click eliminated |
| v14 | 65 | 0 | 2.68s | — | — | — | UnboundLocalError fixed |
| v11 | 10 | 0 | 2.09s | — | — | 0% | Compose-and-abort + skip checkout nav landed |
| v6 | 88 | 0 | 2.68s | — | — | 0% | Phase 4b API path on |
| v1 | — | — | 17s | — | — | — | Original full-DOM flow |

#!/usr/bin/env python3
"""
Long-idle-then-purchase soak test.

Reproduces the EXACT failure mode you're worried about:
  1. Bot runs idle for N minutes (warmup loop only, no purchases)
  2. After idle period, simulate "product just went in stock"
  3. Fire a real ATC POST against Target to prove the warmup-fed cache
     produces a non-403 response
  4. Stop BEFORE actually placing the order (Place Order is monkey-patched
     to no-op, so no real money is spent)

If this test passes after a 2-hour idle, the overnight failure mode is
verifiably fixed: cache stays fresh, ATC succeeds, no false negatives.

USAGE:
    # Quick test (5 min idle)
    python3 tests/test_long_idle_then_purchase.py

    # Match the actual overnight gap (2h47m → ~167 min)
    IDLE_MINUTES=170 python3 tests/test_long_idle_then_purchase.py

    # Skip the real ATC (cache-only check)
    SKIP_ATC=1 python3 tests/test_long_idle_then_purchase.py

    # Use a different TCIN
    TARGET_TEST_TCIN=50270379 python3 tests/test_long_idle_then_purchase.py

WHAT IT VERIFIES:
  ✓ Warmup loop fires every ~24s for the entire idle period
  ✓ Shape headers cache rotates (no "Preserved cache forever" lockup)
  ✓ headers_age never climbs past Shape's TTL (<120s)
  ✓ Real RedSky stock check still works at the end of idle
  ✓ Real ATC POST succeeds with the warmup-fed cache (HTTP 200/201)
  ✓ Place Order does NOT fire (no real charge)

WHAT YOU PAY FOR:
  - 1 cart-add for the test TCIN (gum, ~$2). Will sit in your cart until
    cleared. Either ignore it or run the bot once after this test — its
    `_clear_cart` fires on next purchase boot.
  - Real Target API requests during the idle period (warmup POSTs every
    24s = ~150 POSTs/hour). Well under any rate limit.

EXIT CODES:
  0 — full pipeline healthy through idle + ATC
  1 — warmup degraded during idle (cache locked, the overnight bug)
  2 — ATC failed despite fresh cache (separate bug exists)
  3 — setup error
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── Config from env ─────────────────────────────────────────────────────────
IDLE_MINUTES = float(os.environ.get('IDLE_MINUTES', '5'))
TARGET_TEST_TCIN = os.environ.get('TARGET_TEST_TCIN', '50270379')
WARMUP_INTERVAL_S = float(os.environ.get('WARMUP_INTERVAL_S', '24'))  # match production
SKIP_ATC = os.environ.get('SKIP_ATC') == '1'

# Warmup health thresholds
MAX_HEADERS_AGE_S = 120.0   # Shape rotates ~90-120s; never let cache get older
MAX_PRESERVED_RUN = 3       # consecutive Preserved cache messages = locked


# ── Helpers ─────────────────────────────────────────────────────────────────
def banner(msg, char='='):
    print()
    print(char * 72)
    print(msg)
    print(char * 72)


# ── State tracking ──────────────────────────────────────────────────────────
class HealthMonitor:
    """Tracks warmup health metrics across the idle period."""
    def __init__(self):
        self.cycle_count = 0
        self.cache_updates = 0
        self.cache_preserves = 0
        self.consecutive_preserves = 0
        self.max_consecutive_preserves = 0
        self.max_headers_age_seen = 0.0
        self.violations = []  # list of (cycle_idx, msg)

    def record_cycle(self, headers_age, was_updated, was_preserved):
        self.cycle_count += 1
        if was_updated:
            self.cache_updates += 1
            self.consecutive_preserves = 0
        if was_preserved:
            self.cache_preserves += 1
            self.consecutive_preserves += 1
            self.max_consecutive_preserves = max(self.max_consecutive_preserves,
                                                 self.consecutive_preserves)
        if headers_age > self.max_headers_age_seen:
            self.max_headers_age_seen = headers_age

        # Violation checks
        if headers_age > MAX_HEADERS_AGE_S:
            self.violations.append(
                (self.cycle_count, f"headers_age={headers_age:.0f}s exceeds {MAX_HEADERS_AGE_S:.0f}s — cache locked"))
        if self.consecutive_preserves > MAX_PRESERVED_RUN:
            self.violations.append(
                (self.cycle_count, f"{self.consecutive_preserves} consecutive Preserved cache events — cache locked"))

    def report(self):
        print(f"  Cycles run:                 {self.cycle_count}")
        print(f"  Cache updates:              {self.cache_updates}")
        print(f"  Cache preserves (no-op):    {self.cache_preserves}")
        print(f"  Max consecutive preserves:  {self.max_consecutive_preserves}")
        print(f"  Max headers_age observed:   {self.max_headers_age_seen:.0f}s")
        print(f"  Violations:                 {len(self.violations)}")
        for idx, msg in self.violations[:10]:
            print(f"    cycle {idx}: {msg}")
        if len(self.violations) > 10:
            print(f"    ... and {len(self.violations) - 10} more")


# ── Phase 1: idle warmup loop ──────────────────────────────────────────────
async def phase_1_idle(executor, monitor: HealthMonitor):
    banner(f"PHASE 1 — Idle warmup soak ({IDLE_MINUTES:.1f} minutes)")
    end_time = time.time() + IDLE_MINUTES * 60
    next_warmup = time.time()

    while time.time() < end_time:
        if time.time() >= next_warmup:
            ts_before = executor._cached_cart_headers_ts
            cycle_idx = monitor.cycle_count + 1
            elapsed_min = (time.time() - (end_time - IDLE_MINUTES * 60)) / 60
            print(f"  [{elapsed_min:5.1f}min] cycle {cycle_idx}: firing warm_shape_headers()...")

            try:
                ok = await executor.warm_shape_headers()
            except Exception as e:
                print(f"           !! warm_shape_headers threw: {e!r}")
                monitor.violations.append((cycle_idx, f"exception: {e!r}"))
                ok = False

            ts_after = executor._cached_cart_headers_ts
            headers_age = time.time() - ts_after if ts_after else 999
            was_updated = ts_after > ts_before
            was_preserved = ok and not was_updated

            monitor.record_cycle(headers_age, was_updated, was_preserved)

            shape_count = len([h for h in executor._cached_cart_headers
                              if h.lower().startswith('x-')
                              and h.lower() != 'x-application-name'])
            tag = 'UPDATED' if was_updated else ('PRESERVED' if was_preserved else 'FAILED')
            print(f"           → {tag}, headers_age={headers_age:.1f}s, "
                  f"shape_tokens={shape_count}, total_headers={len(executor._cached_cart_headers)}")

            next_warmup = time.time() + WARMUP_INTERVAL_S

        # Sleep until either next warmup or end of phase
        sleep_until = min(next_warmup, end_time)
        sleep_dur = max(0.5, sleep_until - time.time())
        await asyncio.sleep(min(sleep_dur, 1.0))

    print()
    print("  Phase 1 health report:")
    monitor.report()

    if monitor.violations:
        print()
        print("  RESULT: PHASE 1 FAILED — warmup degraded during idle")
        return False

    print()
    print("  RESULT: PHASE 1 PASSED — warmup stayed healthy through idle")
    return True


# ── Phase 2: real ATC against fresh cache ──────────────────────────────────
async def phase_2_atc(executor, sm) -> dict[str, Any]:
    """Fire a real ATC POST against Target. Place Order is monkey-patched to
    short-circuit, so no real order is placed."""
    banner("PHASE 2 — Real ATC against warmup-fed cache (NO purchase)")

    if SKIP_ATC:
        print("  SKIP_ATC=1 — phase 2 skipped")
        return {'skipped': True}

    # Monkey-patch _place_order and _api_place_order to return False without firing
    original_api_place = executor._api_place_order
    original_dom_place = executor._place_order

    async def stub_api_place(*args, **kwargs):
        print("  [STUB] _api_place_order intercepted — would have placed real order; aborting safely")
        return {'success': False, 'reason': 'test_stub_intercepted',
                'order_id': None, 'confirmation_url': None,
                'status': 0, 'body': ''}

    async def stub_dom_place(*args, **kwargs):
        print("  [STUB] _place_order (DOM path) intercepted — would have clicked Place Order; aborting safely")
        return False

    executor._api_place_order = stub_api_place
    executor._place_order = stub_dom_place

    # Capture interceptor logs so we can inspect ATC response
    import builtins
    log_lines: list[str] = []
    real_print = builtins.print
    def captured_print(*args, **kwargs):
        line = ' '.join(str(a) for a in args)
        log_lines.append(line)
        real_print(*args, **kwargs)
    builtins.print = captured_print

    print(f"  Firing execute_purchase('{TARGET_TEST_TCIN}', quantity=1)...")
    print(f"  (Place Order stubbed — no real order will fire)")
    t0 = time.time()
    try:
        # Disable API place-order so the flow uses DOM path (which we also stub)
        os.environ['TARGET_API_PLACE_ORDER'] = 'false'
        result = await asyncio.wait_for(
            executor.execute_purchase(TARGET_TEST_TCIN, quantity=1),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        result = {'success': False, 'reason': 'timeout', 'aborted': True}
    finally:
        builtins.print = real_print
        executor._api_place_order = original_api_place
        executor._place_order = original_dom_place

    elapsed = time.time() - t0
    print()
    print(f"  execute_purchase returned in {elapsed:.1f}s: {result}")

    # Find the ATC HTTP status from captured logs
    atc_status = None
    for line in log_lines:
        if '[PURCHASE] ATC fetch status:' in line:
            try:
                atc_status = int(line.split('status:')[1].strip().split()[0])
            except Exception:
                pass

    print(f"  ATC HTTP status: {atc_status}")
    print()
    if atc_status in (200, 201):
        print("  RESULT: PHASE 2 PASSED — real ATC succeeded against warmup-fed cache")
        return {'success': True, 'atc_status': atc_status, 'elapsed': elapsed}
    else:
        print(f"  RESULT: PHASE 2 FAILED — ATC status was {atc_status}")
        return {'success': False, 'atc_status': atc_status, 'elapsed': elapsed,
                'reason': result.get('reason', 'unknown')}


# ── Main ────────────────────────────────────────────────────────────────────
async def amain():
    banner("Long-idle soak test — proves overnight fix end-to-end", char='#')
    print(f"  IDLE_MINUTES:       {IDLE_MINUTES}")
    print(f"  WARMUP_INTERVAL_S:  {WARMUP_INTERVAL_S}")
    print(f"  TARGET_TEST_TCIN:   {TARGET_TEST_TCIN}")
    print(f"  SKIP_ATC:           {SKIP_ATC}")
    print()

    from src.session.session_manager import SessionManager
    from src.session.purchase_executor import PurchaseExecutor

    print("  Launching browser session...")
    sm = SessionManager(session_path=str(ROOT / 'target.json'))
    ok = await sm.initialize()
    if not ok:
        print("  ERROR: SessionManager.initialize() returned False")
        return 3
    print("  Browser session ready")

    executor = PurchaseExecutor(sm)
    monitor = HealthMonitor()

    # Prime the cache once before idle starts
    print("  Priming cache (pre-idle warmup)...")
    await executor.warm_shape_headers()
    await asyncio.sleep(3)
    await executor.warm_shape_headers()  # second call to ensure Shape JS attached
    initial_count = len([h for h in executor._cached_cart_headers
                        if h.lower().startswith('x-')
                        and h.lower() != 'x-application-name'])
    print(f"  Cache primed with {initial_count} Shape tokens")
    if initial_count == 0:
        print("  WARNING: Cache primed with 0 Shape tokens. Shape JS may not be loading.")
        print("           Test will continue but ATC phase may fail.")

    # Phase 1
    phase1_ok = await phase_1_idle(executor, monitor)
    if not phase1_ok:
        return 1

    # Phase 2
    phase2_result = await phase_2_atc(executor, sm)
    if phase2_result.get('skipped'):
        banner("RESULT: Phase 1 passed; Phase 2 skipped", char='#')
        return 0
    if not phase2_result.get('success'):
        banner("RESULT: Phase 1 passed but Phase 2 (ATC) FAILED", char='#')
        return 2

    banner("RESULT: ALL PHASES PASSED — overnight failure mode verifiably fixed", char='#')
    return 0


def main():
    try:
        code = asyncio.run(amain())
        sys.exit(code)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FATAL: {e!r}")
        sys.exit(3)


if __name__ == '__main__':
    main()

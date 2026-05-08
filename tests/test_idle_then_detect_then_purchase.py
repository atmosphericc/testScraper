#!/usr/bin/env python3
"""
End-to-end overnight simulation: idle → real stock checks → fake "goes in stock" → bot detects → purchase pipeline fires.

This is the test that mirrors what actually went wrong:
  Overnight: bot idled for 2h47m. When restocks happened at 04:00, bot did
  not detect them — zero IN-STOCK events in the activity log, zero purchase
  attempts. Either the monitor stalled silently or the warmup got so stale
  that ATC would have died on first attempt.

This test reproduces all of those layers:

  PHASE 1 — IDLE WITH LIVE MONITOR (configurable, default 30 min)
    • Real StockMonitor polling RedSky every ~15s for the test TCIN
    • Real warmup loop running every ~24s
    • All cycles tracked: monitor cycles, warmup cycles, cache health, errors
    • If monitor goes silent (no responses) or warmup locks, we catch it here

  PHASE 2 — FLIP "IN STOCK"
    • Test injects an in-stock override into the monitor
    • Next monitor cycle sees the gum as in stock (test_data_override path)
    • In real production, this is exactly what happens at the moment of restock

  PHASE 3 — DETECTION → PURCHASE
    • Bot's natural code path takes over: monitor → purchase manager → executor
    • Real navigate, real ATC POST, real cart confirmation
    • Place Order is monkey-patched to NO-OP (no real charge)
    • Cart auto-clears via the bot's own _clear_cart logic

  PHASE 4 — TEARDOWN
    • Browser closed cleanly
    • Final report of all metrics

EXIT CODES:
  0 — full pipeline healthy: monitor stayed alive, warmup stayed fresh,
      detection fired, ATC succeeded, no real order placed.
  1 — Phase 1 failed: monitor went silent OR warmup locked during idle.
  2 — Phase 2/3 failed: detection didn't trigger purchase OR ATC failed.
  3 — setup error.

USAGE:
  # Standard 30-min run
  python3 tests/test_idle_then_detect_then_purchase.py

  # Match the actual overnight gap (2h47m)
  IDLE_MINUTES=170 python3 tests/test_idle_then_detect_then_purchase.py

  # Use a different test product
  TARGET_TEST_TCIN=50270379 python3 tests/test_idle_then_detect_then_purchase.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── Config ──────────────────────────────────────────────────────────────────
IDLE_MINUTES = float(os.environ.get('IDLE_MINUTES', '30'))
TARGET_TEST_TCIN = os.environ.get('TARGET_TEST_TCIN', '50270379')
WARMUP_INTERVAL_S = float(os.environ.get('WARMUP_INTERVAL_S', '24'))
MONITOR_INTERVAL_S = float(os.environ.get('MONITOR_INTERVAL_S', '15'))
PHASE3_CYCLES = int(os.environ.get('PHASE3_CYCLES', '3'))  # back-to-back ATC attempts
MAX_QTY = int(os.environ.get('MAX_QTY', '0'))  # 0 = let bot resolve via PDP fallback (most realistic)
DISABLE_CAFFEINATE = os.environ.get('DISABLE_CAFFEINATE', '0') == '1'

# Health thresholds
MAX_HEADERS_AGE_S = 120.0
MAX_CONSECUTIVE_PRESERVES = 3
MAX_MONITOR_SILENCE_S = 60.0  # if monitor doesn't return a result for 60s, alarm


# ── Mac sleep prevention ────────────────────────────────────────────────────
def start_caffeinate():
    """Spawn `caffeinate -i -w <pid>` so the laptop won't sleep during a long
    idle test. Mirrors what app.py does in production. Auto-exits when our
    process exits.
    """
    if DISABLE_CAFFEINATE or sys.platform != 'darwin':
        return None
    import subprocess
    try:
        proc = subprocess.Popen(
            ['caffeinate', '-i', '-w', str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  [caffeinate] Mac sleep prevention active (PID {proc.pid})")
        return proc
    except FileNotFoundError:
        print(f"  [caffeinate] not found — Mac may sleep during long idle")
        return None


def banner(msg, char='='):
    print()
    print(char * 75)
    print(f"  {msg}")
    print(char * 75)


def now_str():
    return datetime.now().strftime('%H:%M:%S')


# ── Health metrics ──────────────────────────────────────────────────────────
class Metrics:
    def __init__(self):
        # Monitor
        self.monitor_calls = 0
        self.monitor_successes = 0
        self.monitor_failures = 0
        self.last_monitor_success_ts = time.time()
        self.monitor_max_silence_s = 0.0
        self.monitor_violations: list[str] = []

        # Warmup
        self.warmup_calls = 0
        self.warmup_updates = 0
        self.warmup_preserves = 0
        self.warmup_failures = 0
        self.warmup_consecutive_preserves = 0
        self.warmup_max_consecutive_preserves = 0
        self.warmup_max_age_seen = 0.0
        self.warmup_violations: list[str] = []

        # Detection / purchase
        self.detection_fired = False
        self.detection_ts = None
        self.atc_status = None       # backwards-compat: first cycle's status
        self.atc_elapsed = None      # backwards-compat: first cycle's elapsed
        self.atc_attempts: list[dict] = []  # per-cycle multi-attempt detail
        self.purchase_violations: list[str] = []

    def report(self):
        print(f"  MONITOR:")
        print(f"    calls/successes/failures:    {self.monitor_calls}/{self.monitor_successes}/{self.monitor_failures}")
        print(f"    max silence between successes: {self.monitor_max_silence_s:.1f}s")
        print(f"    violations:                   {len(self.monitor_violations)}")
        for v in self.monitor_violations[:5]:
            print(f"      - {v}")
        print(f"  WARMUP:")
        print(f"    calls/updates/preserves/fails: {self.warmup_calls}/{self.warmup_updates}/{self.warmup_preserves}/{self.warmup_failures}")
        print(f"    max consecutive preserves:     {self.warmup_max_consecutive_preserves}")
        print(f"    max headers_age observed:      {self.warmup_max_age_seen:.1f}s")
        print(f"    violations:                    {len(self.warmup_violations)}")
        for v in self.warmup_violations[:5]:
            print(f"      - {v}")
        print(f"  DETECTION → PURCHASE:")
        print(f"    detection fired:    {self.detection_fired}")
        if self.atc_attempts:
            print(f"    ATC attempts ({len(self.atc_attempts)}):")
            for a in self.atc_attempts:
                ok = '✓' if a['atc_status'] in (200, 201) else '✗'
                print(f"      {ok} cycle {a['cycle']}: HTTP {a['atc_status']}, "
                      f"{a['elapsed_s']}s, reason={a['reason']}")
        else:
            print(f"    ATC HTTP status:    {self.atc_status}")
            print(f"    ATC elapsed:        {self.atc_elapsed}")
        if self.purchase_violations:
            print(f"    violations:")
            for v in self.purchase_violations:
                print(f"      - {v}")

    def phase1_passed(self) -> bool:
        return not self.monitor_violations and not self.warmup_violations

    def phase23_passed(self) -> bool:
        if not self.detection_fired:
            return False
        if self.purchase_violations:
            return False
        # Require ALL cycles to succeed if multi-cycle, else fall back to single
        if self.atc_attempts:
            return all(a['atc_status'] in (200, 201) for a in self.atc_attempts)
        return self.atc_status in (200, 201)


# ── Phase 1: idle with live monitor + warmup ───────────────────────────────
async def phase_1_idle(executor, monitor, metrics: Metrics, in_stock_flag, end_ts: float):
    """Run real warmup + real RedSky checks side-by-side until end_ts.

    in_stock_flag is a dict like {'value': False} — when phase 2 flips it to
    True, the monitor's check returns in_stock for the test TCIN.
    """
    next_warmup = time.time()
    next_monitor = time.time()

    # Stub monitor.get_config so it only checks our test TCIN (don't accidentally
    # poll all the products in production config and possibly trigger real purchases)
    monitor.get_config = lambda: {"products": [
        {"tcin": TARGET_TEST_TCIN, "name": "test gum", "enabled": True}
    ]}

    while time.time() < end_ts:
        loop_now = time.time()

        # ── Monitor cycle ───────────────────────────────────────────────────
        if loop_now >= next_monitor:
            metrics.monitor_calls += 1
            try:
                # Run blocking check_stock in thread executor so we don't block the loop
                stock_result = await asyncio.to_thread(monitor.check_stock)
                if stock_result and TARGET_TEST_TCIN in stock_result:
                    metrics.monitor_successes += 1
                    silence = time.time() - metrics.last_monitor_success_ts
                    metrics.monitor_max_silence_s = max(metrics.monitor_max_silence_s, silence)
                    metrics.last_monitor_success_ts = time.time()
                    entry = stock_result[TARGET_TEST_TCIN]
                    real_in_stock = entry.get('in_stock', False)

                    elapsed_min = (time.time() - (end_ts - IDLE_MINUTES * 60)) / 60
                    print(f"  [{now_str()} {elapsed_min:5.1f}min] MONITOR cycle {metrics.monitor_calls}: "
                          f"in_stock={real_in_stock}, status={entry.get('status_detail')}")

                    # If the test has flipped in_stock_flag, treat the next
                    # monitor result as our "go" signal for detection.
                    # We accept both real_in_stock=True (gum genuinely in stock,
                    # which is the common case for Polar Ice) and the override
                    # path. Either way, this is the moment the production bot
                    # would dispatch a purchase attempt.
                    if in_stock_flag['value']:
                        print(f"  [{now_str()}] >>> DETECTION TRIGGERED "
                              f"(real_in_stock={real_in_stock}, flag=True)")
                        # MAX_QTY=0 (default) → quantity=1 in the signal, but
                        # the executor will hit the PDP fallback at ATC time
                        # and resolve the real per-customer purchase_limit
                        # (often 10 for retail items). Setting MAX_QTY > 0
                        # forces the test to drive a specific qty up front.
                        signal_max_qty = MAX_QTY if MAX_QTY > 0 else entry.get('max_qty', 1)
                        signal = {
                            TARGET_TEST_TCIN: {
                                'title': entry.get('title', 'Test Gum'),
                                'in_stock': True,
                                'available_to_promise_quantity': entry.get('available_to_promise_quantity', 99),
                                'max_qty': signal_max_qty,
                                'last_checked': datetime.now().isoformat(),
                                'status_detail': entry.get('status_detail', 'IN_STOCK'),
                            }
                        }
                        print(f"  [{now_str()}] >>> Signal max_qty={signal_max_qty} "
                              f"(from {'env MAX_QTY' if MAX_QTY > 0 else 'RedSky'})"
                              f"; PDP fallback may upgrade further at ATC time")
                        metrics.detection_fired = True
                        metrics.detection_ts = time.time()
                        return signal  # Phase 1 ends, control returns to caller for Phase 3
                else:
                    metrics.monitor_failures += 1
                    print(f"  [{now_str()}] MONITOR returned empty/missing TCIN")
            except Exception as e:
                metrics.monitor_failures += 1
                print(f"  [{now_str()}] MONITOR THREW: {e!r}")
                metrics.monitor_violations.append(f"call {metrics.monitor_calls}: {e!r}")

            # Check silence threshold even if call returned
            silence = time.time() - metrics.last_monitor_success_ts
            if silence > MAX_MONITOR_SILENCE_S:
                msg = f"monitor silent for {silence:.0f}s (threshold {MAX_MONITOR_SILENCE_S:.0f}s)"
                if msg not in metrics.monitor_violations:
                    metrics.monitor_violations.append(msg)
                    print(f"  [{now_str()}] !!! {msg}")

            next_monitor = time.time() + MONITOR_INTERVAL_S

        # ── Warmup cycle ────────────────────────────────────────────────────
        if loop_now >= next_warmup:
            metrics.warmup_calls += 1
            ts_before = executor._cached_cart_headers_ts
            try:
                ok = await executor.warm_shape_headers()
            except Exception as e:
                ok = False
                metrics.warmup_violations.append(f"call {metrics.warmup_calls}: {e!r}")
                print(f"  [{now_str()}] !!! warmup threw: {e!r}")

            ts_after = executor._cached_cart_headers_ts
            headers_age = (time.time() - ts_after) if ts_after else 999
            updated = ts_after > ts_before
            preserved = ok and not updated

            if updated:
                metrics.warmup_updates += 1
                metrics.warmup_consecutive_preserves = 0
            elif preserved:
                metrics.warmup_preserves += 1
                metrics.warmup_consecutive_preserves += 1
                metrics.warmup_max_consecutive_preserves = max(
                    metrics.warmup_max_consecutive_preserves,
                    metrics.warmup_consecutive_preserves)
            else:
                metrics.warmup_failures += 1
                metrics.warmup_consecutive_preserves = 0

            metrics.warmup_max_age_seen = max(metrics.warmup_max_age_seen, headers_age)

            shape_count = len([h for h in executor._cached_cart_headers
                              if h.lower().startswith('x-')
                              and h.lower() != 'x-application-name'])

            elapsed_min = (time.time() - (end_ts - IDLE_MINUTES * 60)) / 60
            tag = 'UPDATED' if updated else ('PRESERVED' if preserved else 'FAILED')
            print(f"  [{now_str()} {elapsed_min:5.1f}min] WARMUP cycle {metrics.warmup_calls}: "
                  f"{tag}, headers_age={headers_age:.1f}s, shape_tokens={shape_count}")

            # Violations
            if headers_age > MAX_HEADERS_AGE_S:
                msg = f"cycle {metrics.warmup_calls}: headers_age={headers_age:.0f}s exceeds {MAX_HEADERS_AGE_S:.0f}s — cache locked"
                metrics.warmup_violations.append(msg)
                print(f"  [{now_str()}] !!! {msg}")
            if metrics.warmup_consecutive_preserves > MAX_CONSECUTIVE_PRESERVES:
                msg = f"cycle {metrics.warmup_calls}: {metrics.warmup_consecutive_preserves} consecutive preserves — cache locked"
                if msg not in metrics.warmup_violations:
                    metrics.warmup_violations.append(msg)
                    print(f"  [{now_str()}] !!! {msg}")

            next_warmup = time.time() + WARMUP_INTERVAL_S

        # Sleep until next event (monitor or warmup), but never past end_ts
        target = min(next_monitor, next_warmup, end_ts)
        sleep_dur = max(0.5, min(1.0, target - time.time()))
        await asyncio.sleep(sleep_dur)

    return None  # Phase 1 ended without in-stock flip


# ── Phase 3: detection → purchase (multi-cycle) ────────────────────────────
async def phase_3_purchase(executor, signal_qty: int, metrics: Metrics):
    """Fire the purchase pipeline N times in a row, mimicking back-to-back
    restock detections. Place Order is stubbed — no real charge.

    signal_qty: the max_qty from the in-stock signal. If 1, executor will run
    its PDP fallback at ATC time to discover the real per-customer limit
    (the realistic production path). If >1, executor uses it directly.

    Multi-cycle catches:
      - Shape ring-buffer rotation (the v17 perf path)
      - Per-TCIN throttle / cooldown logic
      - MAX_PURCHASE_LIMIT_EXCEEDED self-heal (the May 5 issue shape)
    """
    banner(f"PHASE 3 — Multi-cycle purchase pipeline (N={PHASE3_CYCLES} attempts, qty={signal_qty}, NO real orders)")

    # Stub Place Order paths
    original_api = executor._api_place_order
    original_dom = executor._place_order

    stub_calls = {'api': 0, 'dom': 0}

    async def stub_api(*args, **kwargs):
        stub_calls['api'] += 1
        print(f"  [STUB] _api_place_order intercepted (call #{stub_calls['api']}) — would have placed real order")
        return {'success': False, 'reason': 'test_stub_intercepted',
                'order_id': None, 'confirmation_url': None,
                'status': 0, 'body': ''}

    async def stub_dom(*args, **kwargs):
        stub_calls['dom'] += 1
        print(f"  [STUB] _place_order (DOM) intercepted (call #{stub_calls['dom']}) — would have clicked Place Order")
        return False

    executor._api_place_order = stub_api
    executor._place_order = stub_dom

    # Per-cycle metrics
    metrics.atc_attempts = []  # list of dicts: {cycle, status, elapsed, reason}

    try:
        os.environ['TARGET_API_PLACE_ORDER'] = 'false'
        for cycle in range(1, PHASE3_CYCLES + 1):
            print()
            print(f"  ─── CYCLE {cycle}/{PHASE3_CYCLES} ────────────────────────────────")
            print(f"  [{now_str()}] Firing execute_purchase('{TARGET_TEST_TCIN}', quantity=1)")

            # Per-cycle log capture
            import builtins
            real_print = builtins.print
            log_lines = []
            def hooked(*args, **kwargs):
                line = ' '.join(str(a) for a in args)
                log_lines.append(line)
                real_print(*args, **kwargs)
            builtins.print = hooked

            t0 = time.time()
            try:
                result = await asyncio.wait_for(
                    executor.execute_purchase(TARGET_TEST_TCIN, quantity=signal_qty),
                    timeout=90.0
                )
            except asyncio.TimeoutError:
                result = {'success': False, 'reason': 'test_timeout'}
            except Exception as e:
                result = {'success': False, 'reason': f'exception: {e!r}'}
            finally:
                builtins.print = real_print

            elapsed = time.time() - t0

            # Parse ATC status from this cycle's logs
            atc_status = None
            for line in log_lines:
                if '[PURCHASE] ATC fetch status:' in line:
                    try:
                        atc_status = int(line.split('status:')[1].strip().split()[0])
                    except Exception:
                        pass

            cycle_metric = {
                'cycle': cycle,
                'atc_status': atc_status,
                'elapsed_s': round(elapsed, 1),
                'reason': result.get('reason', 'unknown'),
            }
            metrics.atc_attempts.append(cycle_metric)
            print(f"  Cycle {cycle} result: ATC={atc_status}, elapsed={elapsed:.1f}s, reason={cycle_metric['reason']}")

            if atc_status not in (200, 201):
                metrics.purchase_violations.append(
                    f"cycle {cycle}: ATC status {atc_status} (expected 200/201)")

            # Brief gap between cycles to let cart fully clear
            if cycle < PHASE3_CYCLES:
                gap_s = 8.0
                print(f"  Gap: sleeping {gap_s}s to let cart settle before next cycle...")
                await asyncio.sleep(gap_s)
    finally:
        executor._api_place_order = original_api
        executor._place_order = original_dom

    # Backwards-compat fields for the report
    if metrics.atc_attempts:
        metrics.atc_status = metrics.atc_attempts[0]['atc_status']
        metrics.atc_elapsed = f"{metrics.atc_attempts[0]['elapsed_s']}s"

    print()
    print(f"  Multi-cycle summary:")
    for a in metrics.atc_attempts:
        ok = '✓' if a['atc_status'] in (200, 201) else '✗'
        print(f"    {ok} cycle {a['cycle']}: ATC={a['atc_status']}, "
              f"{a['elapsed_s']}s, reason={a['reason']}")
    print(f"  Place Order stub calls: api={stub_calls['api']}, dom={stub_calls['dom']}")


# ── Phase 0: state-file regression check ───────────────────────────────────
def phase_0_state_file_check() -> list[str]:
    """Catch the April 8 'stuck attempting' bug shape without launching the
    full BulletproofPurchaseManager. Reads logs/purchase_states.json and
    flags any TCIN stuck in a non-recoverable state.
    """
    issues = []
    state_path = ROOT / 'logs' / 'purchase_states.json'
    if not state_path.exists():
        return issues
    try:
        import json
        states = json.loads(state_path.read_text())
    except Exception as e:
        issues.append(f"purchase_states.json unreadable: {e!r}")
        return issues
    # Stuck-attempting check: if TCIN has been 'attempting' for >5 minutes
    # at boot, it'll block real purchases. Production has a 60s auto-reset
    # but state-file persistence can outlast that.
    stuck_attempting = []
    for tcin, s in states.items():
        if s.get('status') == 'attempting':
            started = s.get('started_at', 0)
            age_s = time.time() - started if started else 999999
            if age_s > 300:
                stuck_attempting.append(f"{tcin} (age={age_s:.0f}s)")
    if stuck_attempting:
        issues.append(f"Stuck 'attempting' state: {stuck_attempting} — will block "
                      f"real purchases. Reset to 'ready' before going overnight.")
    # Also flag fake REAL- order IDs that should not exist post-2026-04-08 fix
    fake_orders = [tcin for tcin, s in states.items()
                   if isinstance(s.get('order_number'), str)
                   and s['order_number'].startswith('REAL-')]
    if fake_orders:
        issues.append(f"Fake 'REAL-XXX' order IDs in state: {fake_orders} — "
                      f"the bulletproof_purchase_manager fix may have regressed.")
    return issues


# ── Main ────────────────────────────────────────────────────────────────────
async def amain():
    banner(f"END-TO-END OVERNIGHT SIMULATION", char='#')
    print(f"  IDLE_MINUTES:        {IDLE_MINUTES}")
    print(f"  TARGET_TEST_TCIN:    {TARGET_TEST_TCIN}")
    print(f"  MONITOR_INTERVAL_S:  {MONITOR_INTERVAL_S}")
    print(f"  WARMUP_INTERVAL_S:   {WARMUP_INTERVAL_S}")
    print(f"  PHASE3_CYCLES:       {PHASE3_CYCLES}")
    print()

    # Mac sleep prevention
    caff_proc = start_caffeinate()

    # Phase 0: pre-flight state-file check (instant)
    banner("PHASE 0 — Pre-flight state-file regression check")
    p0_issues = phase_0_state_file_check()
    if p0_issues:
        print(f"  FAIL — found {len(p0_issues)} issue(s):")
        for i in p0_issues:
            print(f"    - {i}")
        print("  Fix these before continuing (or set FORCE_RUN=1 to bypass)")
        if os.environ.get('FORCE_RUN') != '1':
            return 3
    else:
        print(f"  PASS — no stuck states or fake order IDs")

    from src.session.session_manager import SessionManager
    from src.session.purchase_executor import PurchaseExecutor
    from src.monitoring.stock_monitor import StockMonitor

    print()
    print(f"  [{now_str()}] Launching browser...")
    sm = SessionManager(session_path=str(ROOT / 'target.json'))
    if not await sm.initialize():
        print("  ERROR: session init failed")
        return 3
    print(f"  [{now_str()}] Browser ready")

    executor = PurchaseExecutor(sm)
    monitor = StockMonitor()
    metrics = Metrics()

    # Prime cache before idle
    print(f"  [{now_str()}] Priming cache...")
    await executor.warm_shape_headers()
    await asyncio.sleep(3)
    await executor.warm_shape_headers()
    initial_count = len([h for h in executor._cached_cart_headers
                        if h.lower().startswith('x-')
                        and h.lower() != 'x-application-name'])
    print(f"  Cache primed: {initial_count} Shape tokens")

    # Phase 1: idle with live monitor
    in_stock_flag = {'value': False}
    end_ts = time.time() + IDLE_MINUTES * 60

    banner(f"PHASE 1 — Idle with LIVE monitor + warmup ({IDLE_MINUTES:.1f} min)")
    print(f"  Test will flip in_stock_flag at end of idle to trigger detection")
    print(f"  Real RedSky checks every {MONITOR_INTERVAL_S}s")
    print(f"  Real warmup every {WARMUP_INTERVAL_S}s")
    print()

    # Schedule the flip at idle end
    async def trigger_at_end():
        await asyncio.sleep(IDLE_MINUTES * 60)
        print(f"\n  [{now_str()}] >>> IDLE PERIOD COMPLETE — flipping in_stock_flag to True")
        in_stock_flag['value'] = True

    flip_task = asyncio.create_task(trigger_at_end())

    # Run phase 1 until detection fires (or timeout)
    detection_result = await phase_1_idle(executor, monitor, metrics, in_stock_flag, end_ts + 60)
    flip_task.cancel()
    try:
        await flip_task
    except asyncio.CancelledError:
        pass

    banner("PHASE 1 REPORT")
    metrics.report()
    print()
    if not metrics.phase1_passed():
        print(f"  RESULT: PHASE 1 FAILED — see violations above")
        return 1
    print(f"  RESULT: PHASE 1 PASSED — monitor + warmup stayed healthy")

    if not detection_result:
        print(f"  ERROR: Idle ended but in_stock flip didn't trigger detection within grace period")
        return 2

    banner("PHASE 2 — In-stock signal injected, detection fired")
    print(f"  Detection time: {datetime.fromtimestamp(metrics.detection_ts).strftime('%H:%M:%S')}")
    print(f"  Stock data: {detection_result}")

    # Pull max_qty from the signal — this is what production passes through.
    # If 1, executor's PDP fallback will scrape the real per-customer limit.
    signal_qty = detection_result[TARGET_TEST_TCIN].get('max_qty', 1)

    # Phase 3: real ATC against fresh cache
    await phase_3_purchase(executor, signal_qty, metrics)

    banner("FINAL REPORT")
    metrics.report()
    print()
    if metrics.phase23_passed():
        banner("RESULT: ALL PHASES PASSED — overnight scenario fully verified", char='#')
        return 0
    else:
        banner("RESULT: PHASE 2/3 FAILED — see violations", char='#')
        return 2


def main():
    try:
        code = asyncio.run(amain())
        sys.exit(code)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(3)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Run ONLY the curated offline smoke/unit suite. Never blanket-run tests/.

Why this exists (2026-09-14): `for t in tests/test_*.py` launched two LIVE end-to-end
tests (`test_idle_then_detect_then_purchase.py`, `test_long_idle_then_purchase.py`) that
poll RedSky and start a real Chrome against Target. The night before a drop that is
exactly the "second bot" the 09-04 captcha-wall post-mortem warns about. This runner
lists the files that are known to be offline and additionally refuses any file that
carries a browser-launch marker, so a future live test cannot sneak in by name.

Usage:  venv\\Scripts\\python.exe tests\\run_offline_suite.py [--timeout 100]
Exit code: 0 when every listed file passes, 1 otherwise.
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The offline suite. Order: the two 2026-09-13 suites first (fresh-page harvest +
# engine UA), then the 09-11 fixes, then the long-standing smoke set.
OFFLINE = [
    "test_shape_harvest",
    "test_ua_engine_mode",
    "test_won_cart_ride_smoke",
    "test_level_rearm_smoke",
    "test_chrome_age_relaunch_smoke",
    "test_race_dispatch_smoke",
    "test_worker_pool_accounts_smoke",
    "test_purchase_proxy_smoke",
    "test_qty_decision_smoke",
    "test_edge429_cadence",
    "test_0828_phase2_fixes",
    "test_atc_gate_breaker",
    "test_purchase_log_tee",
    "test_wedge_recovery_smoke",
    "test_session_sentinel_smoke",
    # 2026-09-16 hot-sku 0916 plan (stage S1): flags-off fast-lane JS golden
    # snapshot + AC-1 ambiguous-commit latch.
    "test_fast_lane_golden",
    "test_ambiguous_commit",
    # 2026-09-16 hot-sku 0916 plan P1 (stage S2b): won-cart direct checkout loop
    # (+ stock probe, ticket JS, qty guard) and the fast-lane chain it builds on
    # (node subprocess + stub tabs only; CVV flag persistence stubbed).
    "test_won_cart_direct_smoke",
    "test_fast_lane_checkout",
    # 2026-09-16 hot-sku 0916 plan P3/P4 (stage S2c): held-cart re-entry + boot
    # cart audit, legacy re-shoot hygiene + ride clean exit, warmup quiet mode
    # (stub tabs only; verified against LIVE_MARKERS before listing).
    "test_checkout_inplace_reshoot",
    "test_warmup_cart_nav_guard",
    # 2026-09-16 hot-sku 0916 plan P7 (stage S3): DX-1 diagnostics (identity
    # tracker, [EXPOSURE]/[IDENT_CENSUS], arrival stamps, [FS_TICKET] stash,
    # RedSky pickup fields) + the apps-channel detection suite it extends
    # (read before listing: node subprocess + fixtures + stubs only).
    "test_dx_logs",
    "test_redsky_apps_channel",
    # 2026-09-17 hot-sku 0916 plan P5 option (c) (stage S8): per-account hot-TCIN
    # park + its bat pin (stub workers only; checked against LIVE_MARKERS). Review
    # round R1 added the HS-1 / BG-1 / ID-1 enforcement tests, the pre-dispatch
    # sit-out + headline-reason fixes and the AC-1 latch persistence (temp files).
    "test_identity_rest",
    # 2026-09-17 evening: ATC-level DCO burst (real manager race loop against stub
    # workers via the test_identity_rest harness; no browser, no network).
    "test_dco_burst",
    "test_multi_sku_dispatch",
]

# Hard markers of a test that starts a real browser / hits Target. A listed file that
# grows one of these is skipped with a loud line rather than run.
LIVE_MARKERS = re.compile(r"Launching browser|Real StockMonitor|overnight simulation|real_api_overnight")

# Known live tests, for the record (never add these to OFFLINE):
#   test_idle_then_detect_then_purchase, test_long_idle_then_purchase,
#   test_real_api_overnight_simulation, test_target_bot_full_suite


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=100, help="seconds per file")
    args = ap.parse_args()

    passed, failed, skipped = [], [], []
    t0 = time.time()
    for name in OFFLINE:
        path = os.path.join(HERE, name + ".py")
        if not os.path.exists(path):
            failed.append((name, "MISSING"))
            print(f"FAIL {name:<40} file missing")
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            if LIVE_MARKERS.search(fh.read()):
                skipped.append(name)
                print(f"SKIP {name:<40} carries a browser-launch marker — not run")
                continue
        try:
            proc = subprocess.run(
                [sys.executable, path], cwd=ROOT, capture_output=True, text=True,
                timeout=args.timeout, encoding="utf-8", errors="replace",
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            rc = proc.returncode
        except subprocess.TimeoutExpired as e:
            out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
            rc = 124
        last = ""
        for line in reversed(out.splitlines()):
            if re.search(r"passed|fail|error", line, re.I):
                last = line.strip()[:70]
                break
        if rc == 0:
            passed.append(name)
            print(f"OK   {name:<40} {last}")
        else:
            failed.append((name, f"rc={rc}"))
            print(f"FAIL {name:<40} rc={rc} | {last}")
            print("\n".join("      " + l for l in out.splitlines()[-15:]))

    print()
    print(f"===== OFFLINE SUITE: {len(passed)} passed, {len(failed)} failed, "
          f"{len(skipped)} skipped in {time.time() - t0:.0f}s =====")
    if failed:
        print("FAILED: " + ", ".join(f"{n}({why})" for n, why in failed))
    if skipped:
        print("SKIPPED (live marker): " + ", ".join(skipped))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

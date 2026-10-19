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
    # 2026-10-01 FX-1001-A held-line strike (node subprocess + stub executors only;
    # no browser, no network).
    "test_held_line_strike",
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
    # 2026-09-23 post-run: out-of-stock stop, the stuck-reset live-racer guard and
    # the WAVE_FIRST_EDGE=0 branch under the real race loop (stub workers and a
    # stubbed stock probe only; no browser, no network).
    "test_retry_oos_stop",
    # 2026-09-25 post-run: RESILIENT_206_LOG, log-only summaries of RedSky 206 bodies
    # (stub session + stubbed urllib; no browser, no network).
    "test_redsky_206_log",
    # 2026-09-25 post-run: TARGET_TOKEN_MINT_LOG, log-only record of Target's answer
    # to the member-token mint (stub tab + SessionManager; no browser, no network).
    "test_token_mint_log",
    # 2026-09-25 first-gate investigation: RESILIENT_FLIP_LOG, a log-only millisecond
    # [STOCK][FLIP] line per out->in read (real ResilientStockChecker, stub callback,
    # temp state dir; no browser, no network).
    "test_redsky_flip_log",
    # 2026-09-30: the run-log event store (tools/events/build.py + q.py) -- synthetic log
    # lines parsed in memory and into a temp SQLite file (no browser, no network, no bot).
    "test_events_parser",
    # 2026-10-05 post-run: RESILIENT_STATUS_LOG + RESILIENT_206_INGEST=shadow, both log-only
    # (real ResilientStockChecker / StockMonitor parser on synthetic bodies, stubbed
    # urllib, temp state dir; no browser, no network).
    "test_status_log_and_206_shadow",
    # 2026-10-05 gate-hardening audit: PO 2xx guard, foreign-keep, boot skip, edge
    # re-arm, ATC network meta (stubs, temp files and a real checker only).
    "test_gate_hardening_1005",
    # 2026-10-06 mass-restock audit: fair-share dispatch, MISS re-arm, keep-live-failed,
    # wave-first yield, lock-order pin (stub workers / stub checker, the real race loop
    # via the test_identity_rest harness, a temp StateStore; no browser, no network).
    "test_mass_restock_1006",
    "test_fast_cadence_1011",
    # 2026-10-06 run diagnostics, log-only: stall watchdog + [HEALTH] (app functions with stub
    # locks), [STOCK][OTHER], /16 steer INFO, FX-1006 decision lines, bat pins (no browser/network).
    "test_run_diagnostics_1006",
    # 2026-10-08 pre-drop tooling: tools/predrop/boot_check.py + preflight.py on synthetic
    # logs and a temp repo root (host / readiness / git skipped; no browser, no network, no bot).
    "test_predrop_tools",
    # 2026-10-14 post-run: FX-1014-JARGUARD, never write a guest jar over a member's (temp
    # files, a stub tab under SessionManager.save_session_state; no browser, no network).
    "test_jar_guard_1014",
    # 2026-10-14: the PreToolUse hook .claude/hooks/block_bot_launch.py (imports the hook and
    # feeds it command strings; one subprocess of the hook itself; no browser, no network).
    "test_block_bot_launch_hook",
    # 2026-10-14: tools/ops/bot_ctl.py, the start / stop / restart helper /pre-drop boots
    # with (every process / OS call stubbed; nothing launched or signalled).
    "test_bot_ctl",
    # 2026-10-14: tools/ops/ensure_armed.py, the nightly auto-arm + the wrapper's exit-87
    # guest-login hook (every subprocess faked; nothing launched, nothing logged in).
    "test_ensure_armed_1014",
    # 2026-10-19: ENSURE_ARMED_PROFILE_SIGNALS + preflight accounts.profile_guest (C-1018-20; fake runner, temp dirs).
    "test_ensure_armed_profile_1019",
    # 2026-10-14: FAST_SELLING back-off per TCIN (C-1014-15; a bare executor, no browser).
    "test_fs_cooldown_per_tcin_1014",
    # 2026-10-14: FX-1014-JOIN, free accounts join a running race (real race loop, stub workers).
    "test_race_join_1014",
    # 2026-10-18: armed-flag manifest pin (S4) - the bat arms exactly the pinned flags (text only).
    "test_armed_manifest_1018",
    # 2026-10-14: FX-1014-REBUY, non-buyers keep racing a bought TCIN (real dispatch, stub workers).
    "test_rebuy_nonbuyers_1014",
    # 2026-10-14: FX-1014-EGRESS, one account exits a second adapter (loopback sockets only).
    "test_egress_bind_1014",
    # 2026-10-14: FX-1014-GUESTSEEN, the sentinel + boot_check see a GUEST account (stubs only).
    "test_guest_seen_1014",
    # 2026-10-14 late: FX-1014-KICK / SENTSTAGGER / live-guest recovery / egress DNS (stubs, loopback).
    "test_kick_stagger_guest_dns_1014",
    # 2026-10-15: OPS-ORDER-PING, tools/ops/order_watch.py, the read-only order / ambiguous-commit
    # run-log watcher (synthetic lines, temp dirs, one local subprocess of the tool; the real-log
    # replays run only when the main tree's gitignored logs/runs is reachable, else they SKIP).
    "test_order_watch_1015",
    # 2026-10-15 CHK-1: TARGET_CHAIN_NET_META, log-only [CHAIN_NET] wire stamps for pre_checkout /
    # place-order / CVV PUT beside [ATC_NET] (stub Network handlers, temp sqlite for the two saved
    # queries, build.py on synthetic lines; no browser, no network).
    "test_chain_net_meta",
    # 2026-10-15 DCO-PROBE-1015: TARGET_DCO_PROBE_P, the sub-second DCO burst re-POST as a randomized
    # A/B arm (real race loop + the real _check_cart_hold on stub workers / a stub tab, a differential
    # against the pre-change manager via git show; no browser, no network).
    "test_dco_probe_1015",
    # 2026-10-15 S2-LIMKEY-PROBE: TARGET_WARMUP_PROBE_TCINS, the off-window limiter-key probe as a
    # separate warmup-tab evaluate after the heartbeat (real refill loop + interceptor handler + real
    # PurchaseExecutor on stub tabs; flag-off differential against the pre-change executor via git
    # show; no browser, no network).
    "test_limkey_probe_1015",
    # 2026-10-16 EXP-LIMKEY guest experiments: the guest probe's classify / verdict / window /
    # port helpers and the in-window follower's log parsing, log tailing, config read and
    # pre-registered readout (no Chrome, no network, no bot).
    "test_guest_follow_1016",
    # 2026-10-18 S3: the NON-DIRECT forwarder path (Bright Data upstream) on a loopback fake
    # upstream: CONNECT/HTTP auth injection, 502 + stats.errors on a refusing upstream, and a
    # byte differential against `git show 73425568:src/proxy/local_forwarder.py` (loopback only).
    "test_forwarder_upstream_path_1018",
    "test_wall3_chain_1018",
    "test_held_release_429_1018",
    "test_preflight_wu_1018",
    "test_band_coverage_1018",
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

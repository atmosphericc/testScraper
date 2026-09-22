#!/usr/bin/env python3
"""DX-1 (hot-sku 0916 plan P7): diagnostics for the next drop audit — offline.

WHY: the 09-16 forensics had to re-derive per-identity exposure, arrival order,
checkout-ticket admission and pickup availability from raw logs, and some of
it (browser-side arrival time, Target's upstream service time, pickup fields)
was not logged at all. Every piece below is LOG-ONLY and behind its own flag,
default off; with the flags off every existing line and the fast-lane JS are
byte-identical (the JS is also pinned by tests/test_fast_lane_golden.py).

Covers:
  * src/purchasing/identity_rest.py — the pure per-(identity, TCIN) recorder:
    record kinds, run/reset rules, counters, census format, thread safety;
  * manager: [EXPOSURE] (TARGET_EXPOSURE_LOG) on a stand-in manager, the
    tracker gate, [IDENT_CENSUS] (TARGET_IDENT_CENSUS) through the real
    _record_race_result, and the call-site order in the real source;
  * executor: TARGET_FASTLANE_T_STAMPS (JS insertion only when on, node run,
    chain-done ' atc_t0= atc_rt=', [ATC_RESP] ' envoy_ms=' through the real
    interceptor), TARGET_FASTLANE_LOG_CART_QTY (' cart_qty='),
    TARGET_FS_TICKET_LOG (interceptor stash, stash attribution in the loop
    line, the legacy _place_order [FS_TICKET] lines, the per-purchase
    context inside the real _execute_purchase_impl);
  * TARGET_REDSKY_STORE_OPTIONS_LOG: StockMonitor._process_response with the
    real apps fixture (in_stock / max_qty identical on vs off, float ATP kept,
    empty / malformed store_options can never drop a TCIN) and the real sweep
    ingest (TcinStatus.pickup, [STOCK PICKUP] transitions, [STOCK WATCH] suffix).

Offline: no browser, no network (node subprocess only).
Run: python tests/test_dx_logs.py
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

DX_FLAGS = (
    "TARGET_EXPOSURE_LOG", "TARGET_IDENT_CENSUS", "TARGET_IDENTITY_REST",
    "TARGET_IDENTITY_REST_RESET_GAP_S", "TARGET_FASTLANE_T_STAMPS",
    "TARGET_FASTLANE_LOG_CART_QTY", "TARGET_FS_TICKET_LOG",
    "TARGET_REDSKY_STORE_OPTIONS_LOG", "TARGET_STOCK_PROBE",
)
OTHER_KNOBS = ("TARGET_ATC_BYTEMATCH", "TARGET_FASTLANE_QTY_GUARD", "TARGET_WONCART_DIRECT",
               "TARGET_HELD_CART_REENTRY", "TARGET_FASTLANE_STAGE_TRACK",
               "TARGET_CHECKOUT_BODY_CAPTURE", "TARGET_ATC_RESPONSE_HEADER_CAPTURE",
               "TARGET_ATC_RESP_LABEL")
for _k in DX_FLAGS + OTHER_KNOBS:
    os.environ.pop(_k, None)
os.environ["TARGET_API_CAPTURE_CHECKOUT_STEPS"] = "false"   # no capture file writes
os.environ["TARGET_API_CAPTURE_PLACE_ORDER"] = "false"

from zendriver import cdp  # noqa: E402

import src.session.purchase_executor as pe_mod  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402
from src.purchasing import identity_rest as ir  # noqa: E402
from src.monitoring.stock_monitor import StockMonitor, redsky_pickup_fields  # noqa: E402
import src.monitoring.stock_check_resilient as scr_mod  # noqa: E402

TCIN = "1010892069"
OTHER = "1011407490"
FS_KEY = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
HEADERS_JS = json.dumps({"X-GyJwza5Z-a": "tokA", "x-application-name": "web"})
NODE = shutil.which("node")
FIXTURE = ROOT / "tests" / "fixtures" / "redsky_apps_sample.json"
PE_SRC = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")
MGR_SRC = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
SCR_SRC = (ROOT / "src" / "monitoring" / "stock_check_resilient.py").read_text(encoding="utf-8")
SMON_SRC = (ROOT / "src" / "monitoring" / "stock_monitor.py").read_text(encoding="utf-8")

PASSED: list = []
FAILED: list = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


@contextlib.contextmanager
def env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def capture(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    capture.result = r
    return buf.getvalue()


def capture_async(coro):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = asyncio.run(coro)
    capture_async.result = r
    return buf.getvalue()


def lines_with(out, tag):
    return [ln for ln in out.splitlines() if ln.startswith(tag)]


# ═════════════════════════ identity_rest (pure) ═════════════════════════════

def test_ir_classify():
    C = ir.classify_result
    check("ir_success_is_pass", C({"success": True, "reason": "ok"}) == "pass")
    for r in sorted(ir.PASS_REASONS):
        check(f"ir_pass_reason[{r}]", C({"success": False, "reason": r}) == "pass")
    for r in sorted(ir.NOT_RECORDED_REASONS) + ["held_cart_idle_skip", "held_cart_other_tcin"]:
        check(f"ir_not_recorded[{r}]", C({"success": False, "reason": r}) is None)
    check("ir_edge", C({"reason": "rate_limited_429", "gate_kind": "edge"}) == "edge")
    check("ir_dco", C({"reason": "rate_limited_429", "gate_kind": "dco"}) == "dco")
    check("ir_auth401", C({"reason": "atc_failed_api_mode", "gate_kind": "auth401"}) == "auth401")
    check("ir_401_without_gate_kind_is_other", C({"reason": "atc_failed_api_mode"}) == "other")
    check("ir_other_reason", C({"reason": "atc_evaluate_timeout"}) == "other")
    check("ir_unknown_gate_kind_is_other", C({"reason": "x", "gate_kind": "weird"}) == "other")
    check("ir_non_dict_is_other", C(None) == "other" and C("boom") == "other")
    check("ir_reason_case_and_space", C({"reason": " Won_Cart_Held "}) == "pass")
    check("ir_plan_pass_list_complete", {
        "checkout_busy_retryable", "checkout_navigation_failed", "won_cart_held",
        "won_cart_ride_timeout", "won_cart_retired", "cart_qty_cleared"} <= set(ir.PASS_REASONS))
    check("ir_plan_skip_list_complete", {
        "cdp_wedged_pre_atc", "lock_timeout", "ambiguous_commit_latched", "identity_resting",
        "home_share_guard", "account_parked_hot"} <= set(ir.NOT_RECORDED_REASONS))
    # The manager's in-thread skip reason really is the one we skip.
    check("ir_skip_reason_matches_manager", "return 'ambiguous_commit_latched'" in MGR_SRC)
    # R2 review (R2-DX-HELD-PASS): a held-cart re-entry fired no add-to-cart, so
    # none of its results (a placed order included) is a shot.
    for res in ({"success": True, "reason": "ok"}, {"success": False, "reason": "won_cart_held"},
                {"success": False, "reason": "checkout_busy_retryable"},
                {"success": False, "reason": "won_cart_retired"},
                {"success": False, "reason": "checkout_navigation_failed", "ambiguous_commit": True},
                {"success": False, "reason": "held_cart_idle_skip"}):
        tagged = dict(res, woncart_entry="held")
        check(f"ir_held_entry_not_recorded[{res.get('reason')}]", C(tagged) is None, C(tagged))
        check(f"ir_held_entry_case_space[{res.get('reason')}]",
              C(dict(res, woncart_entry=" Held ")) is None)
    check("ir_first_entry_still_pass", C({"success": False, "reason": "won_cart_held",
                                          "woncart_entry": "first"}) == "pass")
    check("ir_untagged_still_pass", C({"success": False, "reason": "won_cart_held"}) == "pass")
    check("ir_held_tag_set_by_executor",
          "_held_res['woncart_entry'] = 'held'" in Path(pe_mod.__file__).read_text(encoding="utf-8"))


def test_ir_flags():
    with env(**{k: None for k in DX_FLAGS}):
        check("ir_tracker_off_by_default", ir.tracker_on() is False
              and ir.exposure_log_on() is False and ir.ident_census_on() is False)
    for f in ("TARGET_EXPOSURE_LOG", "TARGET_IDENT_CENSUS", "TARGET_IDENTITY_REST"):
        with env(**{f: " 1 "}):
            check(f"ir_tracker_on_via[{f}]", ir.tracker_on() is True)
        with env(**{f: "true"}):
            check(f"ir_tracker_not_on_via_true[{f}]", ir.tracker_on() is False)
    check("ir_flag_readers_take_env", ir.exposure_log_on({"TARGET_EXPOSURE_LOG": "1"})
          and ir.ident_census_on({"TARGET_IDENT_CENSUS": "1"})
          and not ir.exposure_log_on({"TARGET_EXPOSURE_LOG": "0"}))
    G = ir.reset_gap_s
    check("ir_gap_default", G({}) == 120.0)
    check("ir_gap_clamp_low", G({"TARGET_IDENTITY_REST_RESET_GAP_S": "10"}) == 30.0)
    check("ir_gap_clamp_high", G({"TARGET_IDENTITY_REST_RESET_GAP_S": "5000"}) == 900.0)
    check("ir_gap_garbage", G({"TARGET_IDENTITY_REST_RESET_GAP_S": "x"}) == 120.0
          and G({"TARGET_IDENTITY_REST_RESET_GAP_S": "nan"}) == 120.0
          and G({"TARGET_IDENTITY_REST_RESET_GAP_S": "inf"}) == 120.0)
    check("ir_gap_strip", G({"TARGET_IDENTITY_REST_RESET_GAP_S": " 150 "}) == 150.0)
    check("ir_from_env", ir.IdentityTracker.from_env({"TARGET_IDENTITY_REST_RESET_GAP_S": "60"}).reset_gap_s == 60.0)


def test_ir_runs_and_counters():
    t = ir.IdentityTracker(reset_gap=120.0)
    A, T = "W3/alt-1", TCIN
    s = [t.record(A, "alt-1", T, "auth401", now=1000.0)]
    s.append(t.record(A, "alt-1", T, "edge", now=1030.0))
    s.append(t.record(A, "alt-1", T, "auth401", now=1117.0))      # 87 s gap: same run
    check("ir_run_counts_up", [x["run_shots"] for x in s] == [1, 2, 3], s)
    check("ir_run_s", [round(x["run_s"]) for x in s] == [0, 30, 117], s)
    s4 = t.record(A, "alt-1", T, "auth401", now=1237.0)          # 120 s gap: new run
    check("ir_gap_120_resets", s4["run_shots"] == 1 and s4["run_s"] == 0.0, s4)
    s5 = t.record(A, "alt-1", T, "dco", now=1250.0)
    check("ir_dco_counted_in_its_run", s5["run_shots"] == 2 and round(s5["run_s"]) == 13, s5)
    check("ir_dco_closes_run", t.exposure(A, T, now=1251.0)["run_shots"] == 0)
    s6 = t.record(A, "alt-1", T, "auth401", now=1260.0)
    check("ir_after_dco_new_run", s6["run_shots"] == 1, s6)
    s7 = t.record(A, "alt-1", T, "pass", now=1270.0)
    check("ir_pass_counted_then_closes", s7["run_shots"] == 2
          and t.exposure(A, T, now=1271.0)["run_shots"] == 0, s7)
    s8 = t.record(A, "alt-1", T, "other", now=1280.0)
    e = t.exposure(A, T, now=1290.0)
    check("ir_exposure_open_run", s8["run_shots"] == 1 and e["run_shots"] == 1
          and round(e["run_s"]) == 10 and e["kind"] is None, e)
    check("ir_exposure_expired_run", t.exposure(A, T, now=1400.0)["run_shots"] == 0)
    check("ir_exposure_unknown", t.exposure("nobody", T, now=1.0) == {"run_shots": 0, "run_s": 0.0, "kind": None})
    check("ir_isolated_by_tcin", t.record(A, "alt-1", OTHER, "auth401", now=1281.0)["run_shots"] == 1)
    check("ir_isolated_by_ident", t.record("W2/business", "business", T, "edge", now=1282.0)["run_shots"] == 1)
    c = t.counters(A, T)
    check("ir_counters", (c["shots"], c["p401"], c["edge"], c["dco"], c["pass"], c["other"])
          == (8, 4, 1, 1, 1, 1) and c["first"] == 1000.0 and c["last"] == 1280.0, c)
    z = t.counters("nobody", T)
    check("ir_counters_unknown_zero", all(z[k] == 0 for k in ir.COUNTER_KEYS), z)
    z["shots"] = 99
    check("ir_counters_is_a_copy", t.counters("nobody", T)["shots"] == 0)
    for i in range(12):
        t.record(A, "alt-1", T, "edge", now=1300.0 + i)
    check("ir_run_kinds_bounded", len(t.run_kinds(A, T)) == ir.IdentityTracker.RUN_KINDS_MAXLEN
          and t.run_kinds("nobody", T) == [])
    check("ir_meta_kept", t._meta[A] == {"acct": "alt-1"})
    t.record(A, "alt-1", T, "edge", now=1320.0, proxied=True)
    check("ir_meta_proxied", t._meta[A].get("proxied") is True)
    check("ir_not_resting_by_default", t.is_resting(A, T, now=1321.0) is False and t.rest_left(A, T, now=1321.0) == 0.0)
    t._rest_until[(A, T)] = 2000.0
    check("ir_resting_hook", t.is_resting(A, T, now=1990.0) is True and t.rest_left(A, T, now=1990.0) == 10.0
          and t.is_resting(A, T, now=2000.0) is False)
    # thread safety
    ts = ir.IdentityTracker()

    def _worker():
        for _ in range(500):
            ts.record("W1/primary", "primary", T, "edge")

    th = [threading.Thread(target=_worker) for _ in range(4)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    check("ir_thread_safe_counts", ts.counters("W1/primary", T)["shots"] == 2000
          and ts.exposure("W1/primary", T)["run_shots"] == 2000)


def test_ir_census_format():
    line = ir.format_census("W2/business", TCIN, {"shots": 10, "p401": 4, "edge": 5, "dco": 0,
                                                  "pass": 1, "other": 0})
    check("ir_census_exact", line == (f"[IDENT_CENSUS] ident=W2/business tcin={TCIN} shots=10 p401=4 "
                                      f"edge=5 dco=0 pass=1 other=0 pass_per_non401=0.167"), line)
    all401 = ir.format_census("W3/alt-1", TCIN, {"shots": 3, "p401": 3})
    check("ir_census_all_401_dash", all401.endswith("pass_per_non401=-") and "edge=0" in all401, all401)
    none = ir.format_census("x", TCIN, None)
    check("ir_census_none", "shots=0" in none and none.endswith("pass_per_non401=-"), none)
    bad = ir.format_census("x", TCIN, {"shots": "x"})
    check("ir_census_garbage_no_raise", bad.startswith("[IDENT_CENSUS] ident=x") and "shots=0" in bad, bad)


# ═════════════════════════════ manager side ═════════════════════════════════

EXP_RE = re.compile(r"^\[EXPOSURE\] ident=(\S+) tcin=(\S+) kind=(\S+) run_shots=(\d+) run_s=(\d+) "
                    r"win_age_s=(\S+) chrome_age_s=(\S+) proxied=(\S+) resting=(\S+)$")
REC = bpm_mod._dx_record_attempt
R401 = {"success": False, "tcin": TCIN, "reason": "atc_failed_api_mode", "gate_kind": "auth401"}
REDGE = {"success": False, "tcin": TCIN, "reason": "rate_limited_429", "gate_kind": "edge"}


def _mgr(status=None, with_monitor=True):
    m = object.__new__(BulletproofPurchaseManager)
    if with_monitor:
        checker = SimpleNamespace(_tcin_status={TCIN: status} if status is not None else {})
        m.stock_monitor = SimpleNamespace(_resilient_checker=checker)
    return m


def _status(now, ws_age=42.0):
    return scr_mod.TcinStatus(tcin=TCIN, in_stock=True, last_checked_at=now,
                              last_true_at=now, window_start_at=(now - ws_age) if ws_age else 0.0)


def _exp_fields(out):
    ls = lines_with(out, "[EXPOSURE]")
    if len(ls) != 1:
        return None
    m = EXP_RE.match(ls[0])
    return m.groups() if m else ("NOMATCH", ls[0])


def test_mgr_exposure():
    now = time.time()
    sm = SimpleNamespace(proxy_url="127.0.0.1:23001", _browser_launched_at=now - 600.0)
    m = _mgr(_status(now))
    with env(**{k: None for k in DX_FLAGS}):
        out = capture(REC, m, "W3/alt-1", "alt-1", TCIN, R401, sm)
    check("mgr_off_silent_and_no_tracker", out == "" and not hasattr(m, "_ident_tracker"), out)

    with env(TARGET_EXPOSURE_LOG="1"):
        f1 = _exp_fields(capture(REC, m, "W3/alt-1", "alt-1", TCIN, R401, sm))
        f2 = _exp_fields(capture(REC, m, "W3/alt-1", "alt-1", TCIN, REDGE, sm))
        f3 = _exp_fields(capture(REC, m, "W3/alt-1", "alt-1", TCIN,
                                 {"success": False, "reason": "held_cart_idle_skip"}, sm))
    check("mgr_exposure_line_shape", f1 is not None and f1[0] != "NOMATCH", f1)
    if f1 and f1[0] != "NOMATCH":
        check("mgr_exposure_fields", f1[:5] == ("W3/alt-1", TCIN, "auth401", "1", "0")
              and 41 <= int(f1[5]) <= 44 and 599 <= int(f1[6]) <= 602
              and f1[7] == "yes" and f1[8] == "no", f1)
    check("mgr_exposure_second_shot", f2 is not None and f2[2] == "edge" and f2[3] == "2", f2)
    check("mgr_exposure_not_recorded_kind_dash", f3 is not None and f3[2] == "-" and f3[3] == "2", f3)
    check("mgr_tracker_counts_only_recorded", m._ident_tracker.counters("W3/alt-1", TCIN)["shots"] == 2)

    with env(TARGET_EXPOSURE_LOG="1"):
        # home IP, no launch stamp
        f = _exp_fields(capture(REC, m, "W1/primary", "primary", TCIN, REDGE,
                                SimpleNamespace(proxy_url=None, _browser_launched_at=0.0)))
        check("mgr_exposure_home_ip", f is not None and f[6] == "-" and f[7] == "no", f)
        f = _exp_fields(capture(REC, m, "legacy", "", TCIN, REDGE, None))
        check("mgr_exposure_no_session_manager", f is not None and f[6] == "-" and f[7] == "-", f)
        f = _exp_fields(capture(REC, _mgr(with_monitor=False), "W1/primary", "primary",
                                TCIN, REDGE, sm))
        check("mgr_exposure_no_monitor_win_age_dash", f is not None and f[5] == "-", f)
        f = _exp_fields(capture(REC, _mgr(_status(now, ws_age=0)), "W1/primary",
                                "primary", TCIN, REDGE, sm))
        check("mgr_exposure_no_window_dash", f is not None and f[5] == "-", f)
        with env(TARGET_STOCK_PROBE="0"):
            f = _exp_fields(capture(REC, _mgr(_status(now)), "W1/primary", "primary",
                                    TCIN, REDGE, sm))
        check("mgr_exposure_probe_off_dash", f is not None and f[5] == "-", f)
        mb = _mgr(_status(now))
        mb.stock_snapshot = lambda *a, **k: 1 / 0
        f = _exp_fields(capture(REC, mb, "W1/primary", "primary", TCIN, REDGE, sm))
        check("mgr_exposure_snapshot_error_still_logs", f is not None and f[5] == "-", f)
        mr = _mgr(_status(now))
        mr._ident_tracker = ir.IdentityTracker()
        mr._ident_tracker._rest_until[("W2/business", TCIN)] = time.time() + 100
        f = _exp_fields(capture(REC, mr, "W2/business", "business", TCIN, R401, sm))
        check("mgr_exposure_resting_yes", f is not None and f[8] == "yes", f)

    # CENSUS / REST alone: record, no line.
    for flag in ("TARGET_IDENT_CENSUS", "TARGET_IDENTITY_REST"):
        mc = _mgr(_status(now))
        with env(**{flag: "1"}):
            out = capture(REC, mc, "W1/primary", "primary", TCIN, REDGE, sm)
        check(f"mgr_records_without_exposure_line[{flag}]", out == ""
              and mc._ident_tracker.counters("W1/primary", TCIN)["shots"] == 1, out)

    # A broken tracker never raises; one line a minute.
    class _Boom:
        def record(self, *a, **k):
            raise RuntimeError("boom")

        def exposure(self, *a, **k):
            raise RuntimeError("boom")

    me = _mgr(_status(now))
    me._ident_tracker = _Boom()
    with env(TARGET_EXPOSURE_LOG="1"):
        o1 = capture(REC, me, "W1/primary", "primary", TCIN, REDGE, sm)
        o2 = capture(REC, me, "W1/primary", "primary", TCIN, REDGE, sm)
    check("mgr_tracker_error_logged_once", "[DX1] record error" in o1 and o2 == "", (o1, o2))


def test_r2_held_reentry_not_a_shot():
    """R2-DX-HELD-PASS, the finding's scenario: 10 edge 429s, 1 real pass (the
    won cart), then 8 held re-entries while the TCIN stays live. The census
    must read shots=11 pass=1 (it read shots=19 pass=9 before the fix)."""
    m = _mgr(_status(time.time()))
    held = {"success": False, "tcin": TCIN, "reason": "won_cart_held", "woncart_entry": "held"}
    real_pass = {"success": False, "tcin": TCIN, "reason": "won_cart_held", "woncart_entry": "first"}
    with env(TARGET_EXPOSURE_LOG="1", TARGET_IDENT_CENSUS="1"):
        for _ in range(10):
            capture(REC, m, "W1/primary", "primary", TCIN, REDGE, None)
        capture(REC, m, "W1/primary", "primary", TCIN, real_pass, None)
        outs = [capture(REC, m, "W1/primary", "primary", TCIN, held, None) for _ in range(8)]
    c = m._ident_tracker.counters("W1/primary", TCIN)
    check("r2h_census_counts", ir.format_census("W1/primary", TCIN, c) ==
          f"[IDENT_CENSUS] ident=W1/primary tcin={TCIN} shots=11 p401=0 edge=10 dco=0 pass=1 "
          f"other=0 pass_per_non401=0.091", ir.format_census("W1/primary", TCIN, c))
    f = _exp_fields(outs[-1])
    check("r2h_exposure_kind_dash", f is not None and f[2] == "-", f)
    # A held re-entry between shots never closes (resets) the identity's run.
    m2 = _mgr(_status(time.time()))
    with env(TARGET_EXPOSURE_LOG="1"):
        for _ in range(3):
            capture(REC, m2, "W3/alt-1", "alt-1", TCIN, R401, None)
        capture(REC, m2, "W3/alt-1", "alt-1", TCIN, held, None)
        f = _exp_fields(capture(REC, m2, "W3/alt-1", "alt-1", TCIN, R401, None))
    check("r2h_run_not_closed_by_held", f is not None and f[2] == "auth401" and f[3] == "4", f)
    check("r2h_run_kinds", m2._ident_tracker.run_kinds("W3/alt-1", TCIN)[-4:] == ["auth401"] * 4,
          m2._ident_tracker.run_kinds("W3/alt-1", TCIN))


def _race_mgr():
    m = object.__new__(BulletproofPurchaseManager)
    m._state_lock = threading.RLock()
    m._states = {TCIN: {"status": "attempting", "started_at": time.time()}}
    m._load_states_unsafe = lambda: m._states

    def _save(states):
        m._states = dict(states)

    m._save_states_unsafe = _save
    m.status_callback = None
    return m


def _race_run(flags):
    m = _race_mgr()
    agg = {"lock": threading.Lock(), "results": {}, "units": 0, "total": 2, "t0": time.time()}
    outs = []
    with env(**flags):
        for ident, acct, res in (("W1/primary", "primary", REDGE), ("W1/primary", "primary", REDGE),
                                 ("W1/primary", "primary", {"success": False, "tcin": TCIN,
                                                            "reason": "checkout_busy_retryable"}),
                                 ("W2/business", "business", R401)):
            capture(REC, m, ident, acct, TCIN, res, None)
        outs.append(capture(m._record_race_result, TCIN, REDGE, agg, "W1/primary"))
        outs.append(capture(m._record_race_result, TCIN, R401, agg, "W2/business"))
    return m, outs


def test_mgr_census():
    m, (o1, o2) = _race_run({"TARGET_IDENT_CENSUS": "1"})
    check("census_not_before_all_done", "[RACE]" in o1 and "[IDENT_CENSUS]" not in o1, o1)
    cl = lines_with(o2, "[IDENT_CENSUS]")
    check("census_one_line_per_racer", len(cl) == 2, o2)
    check("census_primary_counts", any(
        l == (f"[IDENT_CENSUS] ident=W1/primary tcin={TCIN} shots=3 p401=0 edge=2 dco=0 pass=1 "
              f"other=0 pass_per_non401=0.333") for l in cl), cl)
    check("census_business_counts", any(
        l == (f"[IDENT_CENSUS] ident=W2/business tcin={TCIN} shots=1 p401=1 edge=0 dco=0 pass=0 "
              f"other=0 pass_per_non401=-") for l in cl), cl)
    check("census_after_race_line", o2.index("[RACE]") < o2.index("[IDENT_CENSUS]"), o2)
    m0, (p1, p2) = _race_run({})
    check("census_off_silent", "[IDENT_CENSUS]" not in p1 + p2 and "[RACE]" in p2
          and not hasattr(m0, "_ident_tracker"), p2)
    keys = ("status", "race_breakdown", "failure_reason", "accounts_done", "units_bought")
    check("census_state_unchanged", {k: m._states[TCIN].get(k) for k in keys}
          == {k: m0._states[TCIN].get(k) for k in keys}, (m._states, m0._states))
    # EXPOSURE alone never prints a census.
    _, (q1, q2) = _race_run({"TARGET_EXPOSURE_LOG": "1"})
    check("census_needs_its_own_flag", "[IDENT_CENSUS]" not in q2 and len(lines_with(q1 + q2, "[EXPOSURE]")) == 0)

    # The stand-in shape tests/test_race_dispatch_smoke.py passes as `self`
    # (not a manager instance) must keep working with the flag off AND on.
    class _Stub:
        def __init__(self):
            self._state_lock = threading.RLock()
            self._states = {"123": {"status": "attempting", "tcin": "123"}}
            self.status_callback = None

        def _load_states_unsafe(self):
            return dict(self._states)

        def _save_states_unsafe(self, states):
            self._states = states

    for label, flags in (("off", {}), ("on", {"TARGET_IDENT_CENSUS": "1"})):
        stub = _Stub()
        agg = {"lock": threading.Lock(), "total": 1, "started": 0, "finished": 0,
               "results": {}, "units": 0}
        try:
            with env(**flags):
                out = capture(BulletproofPurchaseManager._record_race_result, stub, "123",
                              {"success": False, "reason": "rate_limited_429"}, agg, "W1/primary")
            ok = stub._states["123"]["status"] == "failed" and (
                ("[IDENT_CENSUS] ident=W1/primary tcin=123 shots=0" in out) == (label == "on"))
        except Exception as e:  # noqa: BLE001
            ok, out = False, repr(e)
        check(f"census_plain_stub_self[{label}]", ok, out)


def test_mgr_source_pins():
    S = MGR_SRC
    i_print = S.index("[REAL_PURCHASE_THREAD] [OK] Purchase execution completed")
    i_latch = S.index("self._ac_latch_mark(_skip_ident, tcin, str(result.get('reason') or ''))")
    i_rec = S.index("_dx_record_attempt(self, _skip_ident, _skip_acct, tcin, result,")
    i_retry = S.index("if not _retry_on or result.get('success'):")
    check("pin_record_after_result_print_and_latch", i_print < i_latch < i_rec < i_retry)
    i_skip = S.index("_skip_why = self._thread_skip_reason(")
    i_wait = S.index("result = self._wait_purchase_result(future, target_purchase_executor, tcin)")
    check("pin_skip_breaks_before_record", i_skip < i_wait < i_rec)
    check("pin_record_passes_target_session_manager",
          "_dx_record_attempt(self, _skip_ident, _skip_acct, tcin, result,\n"
          "                                           target_session_manager)" in S)
    body = S[S.index("def _record_race_result"):S.index("def _finalize_purchase_unsafe")]
    check("pin_census_in_record_race_result", "        if all_done:\n"
          "            _dx_ident_census(self, tcin, list(results_snapshot.keys()))" in body
          and body.index("[RACE] {tcin}:") < body.index("_dx_ident_census(self,"))
    check("pin_import", "from . import identity_rest as _ident_rest" in S)


# ═════════════════════════════ executor side ════════════════════════════════

def test_pe_pure_helpers():
    E = pe_mod._dx_epoch_ms
    check("pe_epoch_ms", E(1789000000123) == 1789000000123 and E(1789000000123.7) == 1789000000123)
    check("pe_epoch_ms_rejects", all(E(v) is None for v in (True, 5, "x", None, float("nan"),
                                                            float("inf"), -1)))
    Q = pe_mod._dx_cart_qty
    check("pe_cq_none", Q(None, TCIN) == "-" and Q([], TCIN) == "-" and Q("x", TCIN) == "-")
    check("pe_cq_one", Q([{"tcin": TCIN, "quantity": 2}], TCIN) == "2")
    check("pe_cq_int_tcin_float_qty", Q([{"tcin": int(TCIN), "quantity": 2.0}], TCIN) == "2")
    check("pe_cq_sum", Q([{"tcin": TCIN, "quantity": 1}, {"tcin": TCIN, "quantity": 2}], TCIN) == "3")
    check("pe_cq_foreign_only", Q([{"tcin": OTHER, "quantity": 5}], TCIN) == "-")
    check("pe_cq_bad_qty", Q([{"tcin": TCIN, "quantity": "x"}], TCIN) == "?"
          and Q([{"tcin": TCIN, "quantity": None}], TCIN) == "?"
          and Q([{"tcin": TCIN, "quantity": True}], TCIN) == "?"
          and Q([{"tcin": TCIN, "quantity": float("nan")}], TCIN) == "?")
    check("pe_cq_fraction", Q([{"tcin": TCIN, "quantity": 1.5}], TCIN) == "1.5")
    check("pe_cq_junk_items_skipped", Q([None, "x", {"tcin": TCIN, "quantity": 1}], TCIN) == "1")
    N = pe_mod.fl_chain_dx_note
    A = {"t0": 1789000000000, "t1": 1789000000123, "cart_items": [{"tcin": TCIN, "quantity": 2}]}
    check("pe_note_off", N(A, TCIN, False, False) == "")
    check("pe_note_stamps", N(A, TCIN, True, False) == " atc_t0=1789000000000 atc_rt=123")
    check("pe_note_no_stamps", N({}, TCIN, True, False) == " atc_t0=- atc_rt=-"
          and N(None, TCIN, True, False) == " atc_t0=- atc_rt=-")
    check("pe_note_t0_only", N({"t0": 1789000000000}, TCIN, True, False) == " atc_t0=1789000000000 atc_rt=-")
    check("pe_note_cart_qty", N(A, TCIN, False, True) == " cart_qty=2")
    check("pe_note_both_order", N(A, TCIN, True, True) == " atc_t0=1789000000000 atc_rt=123 cart_qty=2")
    with env(**{k: None for k in DX_FLAGS}):
        check("pe_flags_default_off", not pe_mod.fastlane_t_stamps_on()
              and not pe_mod.fastlane_log_cart_qty_on() and not pe_mod.fs_ticket_log_on())
    with env(TARGET_FASTLANE_T_STAMPS=" 1", TARGET_FASTLANE_LOG_CART_QTY="1 ", TARGET_FS_TICKET_LOG="1"):
        check("pe_flags_on_strip", pe_mod.fastlane_t_stamps_on() and pe_mod.fastlane_log_cart_qty_on()
              and pe_mod.fs_ticket_log_on())


class _SMStub:
    account_id = "primary"

    def __init__(self, cvv=""):
        self._cvv = cvv

    def _load_account_cvv(self):
        return self._cvv


class _CapTab:
    def __init__(self, result=None):
        self.js = None
        self._result = result

    async def evaluate(self, js, await_promise=True):
        self.js = js
        return copy.deepcopy(self._result)


def _bare_pe(cvv_required=False, cvv=""):
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex._cvv_required = cvv_required
    ex._atc_referrer_pdp = False
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._fastlane_placed = False
    ex._fast_selling_until = 0.0
    ex._persist_cvv_challenge_flag = lambda: None
    ex._last_checkout_resp = {}
    ex._fs_legacy_ctx = None
    ex._stock_live_fn = None
    ex.session_manager = _SMStub(cvv)
    ex.logger = logging.getLogger("dx_test_quiet")
    return ex


CHAIN_RES = {"atc": {"status": 201, "body": "", "t0": 1789000000000, "t1": 1789000000123,
                     "cart_items": [{"tcin": TCIN, "quantity": 2}]},
             "pre": {"status": 429}, "po": {"status": 0, "body": "", "fired": False},
             "skip": "pre_429"}


def _chain(flags, result=CHAIN_RES):
    ex = _bare_pe()
    tab = _CapTab(result)
    with env(**flags):
        out = capture_async(ex._api_fast_lane(tab, TCIN, 2, HEADERS_JS))
    ls = lines_with(out, "[FAST_LANE] chain done")
    return (ls[0] if ls else None), tab.js


def test_pe_chain_done_line():
    base = r"\[FAST_LANE\] chain done in \d+\.\d{2}s — atc=201 pre=429 po=0 skip=pre_429 ident=primary"
    line, _ = _chain({})
    check("chain_off_unchanged", line is not None and re.fullmatch(base, line) is not None, line)
    line, _ = _chain({"TARGET_FASTLANE_T_STAMPS": "0", "TARGET_FASTLANE_LOG_CART_QTY": "0"})
    check("chain_explicit_zero_unchanged", line is not None and re.fullmatch(base, line) is not None, line)
    line, _ = _chain({"TARGET_FASTLANE_T_STAMPS": "1"})
    check("chain_t_stamps", line is not None and re.fullmatch(base + " atc_t0=1789000000000 atc_rt=123", line)
          is not None, line)
    line, _ = _chain({"TARGET_FASTLANE_LOG_CART_QTY": "1"})
    check("chain_cart_qty", line is not None and re.fullmatch(base + " cart_qty=2", line) is not None, line)
    line, _ = _chain({"TARGET_FASTLANE_T_STAMPS": "1", "TARGET_FASTLANE_LOG_CART_QTY": "1"})
    check("chain_both", line is not None
          and re.fullmatch(base + " atc_t0=1789000000000 atc_rt=123 cart_qty=2", line) is not None, line)
    nost = copy.deepcopy(CHAIN_RES)
    nost["atc"] = {"status": 201, "body": ""}
    line, _ = _chain({"TARGET_FASTLANE_T_STAMPS": "1", "TARGET_FASTLANE_LOG_CART_QTY": "1"}, nost)
    check("chain_missing_fields_dash", line is not None
          and line.endswith(" ident=primary atc_t0=- atc_rt=- cart_qty=-"), line)


PRE_INS = "\n                out.atc.t0 = Date.now();"
POST_INS = "\n                out.atc.t1 = Date.now();"


def test_pe_js_insertion():
    _, js_off = _chain({})
    _, js_on = _chain({"TARGET_FASTLANE_T_STAMPS": "1"})
    check("js_off_has_no_date_now", "Date.now" not in js_off)
    check("js_on_inserts_once_each", js_on.count(PRE_INS) == 1 and js_on.count(POST_INS) == 1)
    check("js_on_minus_insertions_equals_off", js_on.replace(PRE_INS, "", 1).replace(POST_INS, "", 1) == js_off)
    check("js_t0_immediately_before_atc_fetch", re.search(
        r"try \{\n\s+out\.atc\.t0 = Date\.now\(\);\n\s+const r = await fetch\('https://carts\.target\.com/"
        r"web_checkouts/v1/cart_items", js_on) is not None)
    check("js_t1_right_after_atc_fetch_resolves", re.search(
        r"\}\);\n\s+out\.atc\.t1 = Date\.now\(\);\n\s+const t = await r\.text\(\);", js_on) is not None)
    # golden: explicit '0' for the DX flags renders the 11797839 JS byte-for-byte
    import test_fast_lane_golden as G
    golden = G.parse(G.FIXTURE.read_bytes().decode("utf-8"))
    zeros = {k: "0" for k in ("TARGET_FASTLANE_T_STAMPS", "TARGET_FASTLANE_LOG_CART_QTY",
                              "TARGET_FS_TICKET_LOG", "TARGET_EXPOSURE_LOG")}
    for label, envv, cvv_req, cvv, ref in G.VARIANTS:
        e = dict(envv)
        e.update(zeros)
        js0 = G.render(e, cvv_req, cvv, ref)[0]
        check(f"js_golden_with_dx_zero[{label}]", js0 == golden.get(label))
        e["TARGET_FASTLANE_T_STAMPS"] = "1"
        js1 = G.render(e, cvv_req, cvv, ref)[0]
        check(f"js_golden_plus_stamps_only[{label}]",
              js1.replace(PRE_INS, "", 1).replace(POST_INS, "", 1) == golden.get(label))
    check("js_golden_render_restored_env", all(os.environ.get(k) is None for k in zeros))


JS_HARNESS = r"""
const scenario = %s;
const calls = [];
globalThis.fetch = async (url, opts) => {
  calls.push(String(url).split('?')[0].split('/').pop());
  let rule = null;
  for (const r of scenario) { if (String(url).includes(r.match)) { rule = r; break; } }
  if (!rule) throw new Error('no stub rule for ' + url);
  if (rule.delay) await new Promise(res => setTimeout(res, rule.delay));
  if (rule.throw) throw new Error(rule.throw);
  return { status: rule.status, text: async () => rule.body || '' };
};
(async () => {
  const out = await (%s);
  console.log(JSON.stringify({out, calls}));
})().catch(e => { console.log(JSON.stringify({error: String(e)})); });
"""


def _node_run(flags, scenario):
    _, js = _chain(flags)
    src = JS_HARNESS % (json.dumps(scenario), js)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        p = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            return {"error": p.stderr[:400]}
        return json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


def _strip_stamps(out):
    o = copy.deepcopy(out)
    (o.get("atc") or {}).pop("t0", None)
    (o.get("atc") or {}).pop("t1", None)
    return o


def test_pe_js_node():
    if not NODE:
        check("node_available", False, "node not on PATH")
        return
    atc201 = {"match": "cart_items", "status": 201, "delay": 40,
              "body": json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2})}
    pre429 = {"match": "pre_checkout", "status": 429, "body": json.dumps({"message": FS_KEY})}
    on = _node_run({"TARGET_FASTLANE_T_STAMPS": "1"}, [atc201, pre429])
    off = _node_run({}, [atc201, pre429])
    oa = (on.get("out") or {}).get("atc") or {}
    check("node_on_runs", "error" not in on and "error" not in off, (on, off))
    check("node_on_stamps_numbers", isinstance(oa.get("t0"), int) and isinstance(oa.get("t1"), int)
          and oa["t1"] - oa["t0"] >= 30 and oa["t0"] > 1e12, oa)
    check("node_off_no_stamps", "t0" not in ((off.get("out") or {}).get("atc") or {}), off)
    check("node_on_equals_off_minus_stamps", _strip_stamps(on.get("out") or {}) == (off.get("out") or {})
          and on.get("calls") == off.get("calls") == ["cart_items", "pre_checkout"], (on, off))
    thr = _node_run({"TARGET_FASTLANE_T_STAMPS": "1"},
                    [{"match": "cart_items", "throw": "net down"}, pre429])
    ta = (thr.get("out") or {}).get("atc") or {}
    check("node_atc_throw_t0_only", isinstance(ta.get("t0"), int) and "t1" not in ta
          and thr["out"]["skip"] == "atc_threw", thr)
    r401 = _node_run({"TARGET_FASTLANE_T_STAMPS": "1"},
                     [{"match": "cart_items", "status": 401, "body": "{}"}, pre429])
    ra = (r401.get("out") or {}).get("atc") or {}
    check("node_atc_401_both_stamps", isinstance(ra.get("t0"), int) and isinstance(ra.get("t1"), int)
          and r401["out"]["skip"] == "atc_401" and r401["calls"] == ["cart_items"], r401)
    # combination with the qty guard (both JS insertions at once) still parses and places
    ok_pre = json.dumps({"cart_items": [{"tcin": TCIN, "quantity": 2}], "cart_id": "C1",
                         "payment_instructions": [{"payment_instruction_id": "PI-1"}]})
    combo = _node_run({"TARGET_FASTLANE_T_STAMPS": "1", "TARGET_FASTLANE_QTY_GUARD": "1"},
                      [atc201, {"match": "pre_checkout", "status": 200, "body": ok_pre},
                       {"match": "v1/checkout", "status": 200,
                        "body": json.dumps({"orders": [{"order_id": "OID-1"}]})}])
    co = combo.get("out") or {}
    check("node_combo_with_qty_guard", co.get("po", {}).get("status") == 200
          and co.get("pre", {}).get("qty") == 2 and isinstance(co.get("atc", {}).get("t1"), int)
          and combo.get("calls") == ["cart_items", "pre_checkout", "checkout"], combo)


# ── the real interceptor ──

class _FakeTab:
    def __init__(self):
        self.sent = []
        self.enabled_domains = []
        self.handlers = {}

    async def send(self, gen):
        cmd = next(gen)
        self.sent.append((cmd.get("method", "?"), cmd.get("params", {})))
        try:
            gen.send({})
        except StopIteration:
            pass
        return None

    def add_handler(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def remove_handler(self, handler, event_type):
        self.handlers.get(event_type, []).remove(handler)

    def continues(self):
        return [p.get("requestId") for m, p in self.sent if m == "Fetch.continueRequest"]


def _int_ex():
    ex = _bare_pe()
    ex._cdp_continued_ids = set()
    ex._cdp_dedup_hits = 0
    ex._cached_cart_headers = {}
    ex._cached_cart_headers_ts = 0.0
    ex._shape_capture_ring = []
    return ex


def _resp_event(req_id, url, status, headers):
    return SimpleNamespace(
        request_id=cdp.fetch.RequestId(req_id), response_status_code=status,
        response_error_reason=None,
        response_headers=[SimpleNamespace(name=k, value=v) for k, v in headers.items()],
        request=SimpleNamespace(url=url, method="POST", headers={}))


ATC_URL = "https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART"
PO_URL = "https://carts.target.com/web_checkouts/v1/checkout?cart_type=REGULAR"


def _drive(flags, events, prep=None):
    async def _go():
        ex = _int_ex()
        if prep:
            prep(ex)
        tab = _FakeTab()
        await ex._setup_cdp_fetch_interceptor(tab, persistent=False)
        h = tab.handlers[cdp.fetch.RequestPaused][-1]
        for ev in events:
            await h(ev)
        return ex, tab

    with env(**flags):
        out = capture_async(_go())
    ex, tab = capture_async.result
    return ex, tab, out


def test_pe_interceptor():
    atc = lambda rid, hdrs, st=429: _resp_event(rid, ATC_URL, st, hdrs)  # noqa: E731
    H429 = {"tgt-cart-error-key": "ERR_A2C_TCIN_RATE_LIMITED", "x-request-id": "RID-1",
            "x-envoy-upstream-service-time": "37"}
    ex, tab, out = _drive({}, [atc("a1", H429)])
    al = [l for l in out.splitlines() if "[ATC_RESP]" in l]
    check("atc_resp_off_unchanged", al == ["[INTERCEPTOR:main] [ATC_RESP] status=429 method=POST "
                                           "tgt-cart-error-key=ERR_A2C_TCIN_RATE_LIMITED "
                                           "x-request-id=RID-1 url=cart_items"], al)
    check("atc_resp_continued_once", tab.continues() == ["a1"], tab.continues())
    ex, tab, out = _drive({"TARGET_FASTLANE_T_STAMPS": "1"},
                          [atc("a1", H429), atc("a2", {"x-request-id": "RID-2"}, 201)])
    al = [l for l in out.splitlines() if "[ATC_RESP]" in l]
    check("atc_resp_envoy", len(al) == 2 and al[0].endswith("url=cart_items envoy_ms=37")
          and al[1].endswith("url=cart_items envoy_ms=-"), al)
    check("atc_resp_envoy_continued_once_each", tab.continues() == ["a1", "a2"], tab.continues())

    # 2026-09-21 tab label. OFF is asserted byte-identical above; ON must append
    # at the TAIL only, so the historic prefix and the "url=cart_items envoy_ms="
    # adjacency that six analysis parsers anchor on both survive.
    ex, tab, out = _drive({"TARGET_ATC_RESP_LABEL": "1"}, [atc("a1", H429)])
    al = [l for l in out.splitlines() if "[ATC_RESP]" in l]
    check("atc_resp_label_on", len(al) == 1
          and al[0].startswith("[INTERCEPTOR:main] [ATC_RESP] status=429 method=POST "
                               "tgt-cart-error-key=ERR_A2C_TCIN_RATE_LIMITED "
                               "x-request-id=RID-1 url=cart_items")
          and al[0].endswith(" tab=main selftest=off"), al)
    check("atc_resp_label_continued_once", tab.continues() == ["a1"], tab.continues())
    ex, tab, out = _drive({"TARGET_ATC_RESP_LABEL": "1", "TARGET_FASTLANE_T_STAMPS": "1"},
                          [atc("a1", H429)])
    al = [l for l in out.splitlines() if "[ATC_RESP]" in l]
    check("atc_resp_label_keeps_envoy_adjacency", len(al) == 1
          and "url=cart_items envoy_ms=37 tab=main selftest=off" in al[0], al)

    def _prep431(e):
        e._last_req_hdr_bytes = {"main": {"total": 9000, "cookie": 5000, "shape": 3000,
                                          "a": 2000, "a0": 0, "replayed": True}}
    ex, tab, out = _drive({"TARGET_FASTLANE_T_STAMPS": "1"}, [atc("a3", H429, 431)], prep=_prep431)
    al = [l for l in out.splitlines() if "[ATC_RESP]" in l]
    check("atc_resp_431_then_envoy", len(al) == 1 and "| req_bytes=9000 " in al[0]
          and al[0].endswith("replayed=yes envoy_ms=37"), al)

    HFS = {"tgt-cart-error-key": FS_KEY, "x-envoy-upstream-service-time": "5"}
    ex, tab, out = _drive({}, [_resp_event("c1", PO_URL, 429, HFS)])
    check("stash_off_untouched", ex._last_checkout_resp == {}, ex._last_checkout_resp)
    check("stash_off_reject_fields_as_before", ex._checkout_rejected is True
          and ex._checkout_reject_status == 429 and ex._checkout_reject_reason == FS_KEY)
    t0 = time.time()
    ex, tab, out = _drive({"TARGET_FS_TICKET_LOG": "1"}, [_resp_event("c1", PO_URL, 429, HFS)])
    st = ex._last_checkout_resp
    check("stash_on_fields", st.get("status") == 429 and st.get("key") == FS_KEY and st.get("envoy") == "5"
          and st.get("label") == "main" and t0 <= st.get("ts", 0) <= time.time(), st)
    check("stash_on_reject_fields_as_before", ex._checkout_rejected is True
          and ex._checkout_reject_status == 429 and ex._checkout_reject_reason == FS_KEY
          and tab.continues() == ["c1"])
    ex, tab, out = _drive({"TARGET_FS_TICKET_LOG": "1"}, [_resp_event("c2", PO_URL, 200, {})])
    st = ex._last_checkout_resp
    check("stash_on_success", st.get("status") == 200 and st.get("key") == "" and st.get("envoy") == "-"
          and ex._checkout_rejected is False and tab.continues() == ["c2"], st)
    # an ATC response never writes the checkout stash
    ex, tab, out = _drive({"TARGET_FS_TICKET_LOG": "1"}, [atc("a4", H429)])
    check("stash_not_written_by_atc", ex._last_checkout_resp == {}, ex._last_checkout_resp)


def test_pe_stash_attribution():
    ex = _bare_pe()
    now = time.time()
    check("stash_empty_none", ex._checkout_stash_for(429, now) is None)
    ex._last_checkout_resp = {"ts": now, "status": 429, "key": FS_KEY, "envoy": "9"}
    check("stash_match", ex._checkout_stash_for(429, now - 1) is ex._last_checkout_resp)
    check("stash_status_mismatch", ex._checkout_stash_for(424, now - 1) is None)
    check("stash_bad_status", ex._checkout_stash_for(None, now - 1) is None
          and ex._checkout_stash_for(True, now - 1) is None)
    check("stash_stale", ex._checkout_stash_for(429, now + 0.2) is None)
    check("stash_slack", ex._checkout_stash_for(429, now + 0.04) is not None
          and ex._checkout_stash_for(429, now + 0.2, slack=0.25) is not None)
    ex._last_checkout_resp = {"ts": "x", "status": "y"}
    check("stash_garbage_none", ex._checkout_stash_for(429, now) is None)

    L = {"tcin": TCIN, "cart_id": "CART-1234567890", "tickets": 2, "first_201_ts": now - 10}

    def _line(stash, po, flags=None, js_ms=900):
        e = _bare_pe()
        e._checkout_reject_status = po.get("status", 0)
        e._checkout_reject_reason = FS_KEY
        e._last_checkout_resp = stash
        fl = {"pre": {"status": 200}, "po": po, "js_ms": js_ms}
        with env(**(flags if flags is not None else {"TARGET_FS_TICKET_LOG": "1"})):
            out = capture(e._log_fs_ticket, L, fl, "steady", 45.2, True, "po_only")
        ls = lines_with(out, "[FS_TICKET]")
        return ls[0] if ls else ""

    po = {"fired": True, "status": 429, "t0": int((now - 1.0) * 1000)}
    l1 = _line({"ts": now - 0.8, "status": 429, "envoy": "9"}, po)
    check("loop_line_envoy_attributed", "envoy_ms=9 " in l1 and f"key={FS_KEY}" in l1
          and "layer=po mode=po_only status=429" in l1, l1)
    check("loop_line_envoy_stale", "envoy_ms=- " in _line({"ts": now - 5.0, "status": 429, "envoy": "9"}, po))
    check("loop_line_envoy_status_mismatch",
          "envoy_ms=- " in _line({"ts": now - 0.8, "status": 424, "envoy": "9"}, po))
    po_not0 = {"fired": True, "status": 429}
    check("loop_line_no_t0_uses_js_ms",
          "envoy_ms=9 " in _line({"ts": time.time() - 0.5, "status": 429, "envoy": "9"}, po_not0)
          and "envoy_ms=- " in _line({"ts": time.time() - 3.0, "status": 429, "envoy": "9"}, po_not0))
    check("loop_line_not_fired", "envoy_ms=- " in _line({"ts": now, "status": 0, "envoy": "9"},
                                                        {"fired": False, "status": 0}))
    check("loop_line_flag_off", _line({"ts": now, "status": 429, "envoy": "9"}, po, flags={}) == "")


FS_RE = re.compile(r"^\[FS_TICKET\] ident=primary tcin=(\d+) cart_id=- n=(\d+) cls=(\S+) gap_s=(\S+) "
                   r"ms_since_201=(\S+) live=(\S+) win_age=(\S+) layer=po mode=legacy status=(\d+) "
                   r"key=(\S+) envoy_ms=(\S+) js_ms=(\d+)$")


class _UrlTab:
    url = "https://www.target.com/checkout"


def _legacy_ex(seq, ctx):
    ex = _bare_pe()
    ex._fs_legacy_ctx = ctx
    now = time.time()
    ex._stock_live_fn = lambda t: {"live": True, "window_start": now - 30.0}
    c = {"api": 0}

    async def fake_api(tab):
        c["api"] += 1
        step = seq[min(c["api"] - 1, len(seq) - 1)]
        await asyncio.sleep(0.01)
        if step.get("stash") is not None:
            ex._last_checkout_resp = dict(step["stash"], ts=time.time())
        return dict(step["res"])

    async def fake_warm(force_fresh=False):
        return True

    async def fake_false(tab):
        return False

    async def fake_find(tab):
        return (None, None)

    ex._api_place_order = fake_api
    ex.warm_shape_headers = fake_warm
    ex._handle_busy_modal = fake_false
    ex._find_place_order_button = fake_find
    return ex, c


def _R(status, reason="", success=False, order_id=None):
    return {"success": success, "status": status, "reason": reason, "order_id": order_id,
            "confirmation_url": None, "body": ""}


LEGACY_SEQ = [
    {"res": _R(424, "http_424"), "stash": {"status": 424, "key": "RESERVATION_FAILURE", "envoy": "11"}},
    {"res": _R(424, "http_424"), "stash": None},              # stale stash from shot 1 must not count
    {"res": _R(200, "ok", True, "OID-L"), "stash": {"status": 200, "key": "", "envoy": "13"}},
]
LEGACY_ENV = {"TARGET_API_PLACE_ORDER": "true", "TARGET_CHECKOUT_INPLACE_RETRY_N": "4",
              "TARGET_CHECKOUT_INPLACE_DELAY_MIN": "0.2", "TARGET_CHECKOUT_INPLACE_DELAY_MAX": "0.2"}


def _legacy_run(flags, ctx):
    ex, c = _legacy_ex(LEGACY_SEQ, ctx)
    e = dict(LEGACY_ENV)
    e.update(flags)
    with env(**e):
        out = capture_async(ex._place_order(_UrlTab()))
    return capture_async.result, c["api"], out, ex


def test_pe_legacy_fs_ticket():
    now = time.time()
    ctx = {"tcin": TCIN, "atc_ts": now - 2.0, "n": 0, "last_ts": 0.0}
    r, api, out, ex = _legacy_run({"TARGET_FS_TICKET_LOG": "1"}, ctx)
    ls = lines_with(out, "[FS_TICKET]")
    m = [FS_RE.match(l) for l in ls]
    check("legacy_result_unchanged", r is True and api == 3 and ex._api_order_id == "OID-L", (r, api))
    check("legacy_three_lines", len(ls) == 3 and all(m), ls)
    if len(ls) == 3 and all(m):
        g = [x.groups() for x in m]
        check("legacy_line1", g[0][0] == TCIN and g[0][1] == "1" and g[0][2] == "legacy_first"
              and 1.9 <= float(g[0][3]) <= 2.5 and 1900 <= int(g[0][4]) <= 2500
              and g[0][5] == "True" and g[0][6] == "30s" and g[0][7] == "424"
              and g[0][8] == "RESERVATION_FAILURE" and g[0][9] == "11", g[0])
        check("legacy_line2_stale_stash_ignored", g[1][1] == "2" and g[1][2] == "legacy_reshoot"
              and float(g[1][3]) >= 0.2 and g[1][7] == "424" and g[1][8] == "-" and g[1][9] == "-", g[1])
        check("legacy_line3", g[2][1] == "3" and g[2][7] == "200" and g[2][8] == "-" and g[2][9] == "13"
              and int(g[2][4]) > int(g[1][4]) > int(g[0][4]), g[2])
    check("legacy_ctx_updated", ctx["n"] == 3 and ctx["last_ts"] > now)
    r0, api0, out0, _ = _legacy_run({}, {"tcin": TCIN, "atc_ts": now, "n": 0, "last_ts": 0.0})
    check("legacy_flag_off_silent", "[FS_TICKET]" not in out0 and r0 is True and api0 == 3, out0[-300:])
    r1, api1, out1, _ = _legacy_run({"TARGET_FS_TICKET_LOG": "1"}, None)
    check("legacy_no_ctx_silent", "[FS_TICKET]" not in out1 and r1 is True and api1 == 3)
    r2, api2, out2, _ = _legacy_run({"TARGET_FS_TICKET_LOG": "1"}, {"tcin": TCIN, "n": 0})
    l2 = lines_with(out2, "[FS_TICKET]")
    check("legacy_no_atc_ts_dashes", len(l2) == 3 and "gap_s=- ms_since_201=-" in l2[0]
          and "gap_s=- " not in l2[1], l2)
    # a bare executor (no ctx attribute at all) is silent and unchanged
    ex3, c3 = _legacy_ex(LEGACY_SEQ, None)
    del ex3._fs_legacy_ctx
    with env(**dict(LEGACY_ENV, TARGET_FS_TICKET_LOG="1")):
        out3 = capture_async(ex3._place_order(_UrlTab()))
    check("legacy_bare_executor_silent", "[FS_TICKET]" not in out3 and capture_async.result is True)
    check("pin_legacy_call_sites", PE_SRC.count("self._log_fs_ticket_legacy(api_result, ") == 2
          and "_dx_t = time.time()\n            api_result = await self._api_place_order(tab)\n"
              "            self._log_fs_ticket_legacy(api_result, 'legacy_first', _dx_t)" in PE_SRC
          and "_dx_t = time.time()\n                    api_result = await self._api_place_order(tab)\n"
              "                    self._log_fs_ticket_legacy(api_result, 'legacy_reshoot', _dx_t)" in PE_SRC)


def test_pe_note_atc():
    ex = _bare_pe()
    ex._fs_legacy_ctx = None
    ex._fs_legacy_note_atc({"t1": 1})
    check("note_atc_no_ctx_noop", ex._fs_legacy_ctx is None)
    del ex._fs_legacy_ctx
    ex._fs_legacy_note_atc({})
    check("note_atc_no_attr_noop", not hasattr(ex, "_fs_legacy_ctx"))
    now = time.time()
    for label, atc, lo, hi in (
            ("browser_stamp", {"t1": int((now - 1.0) * 1000)}, now - 1.01, now - 0.99),
            ("stale_stamp_uses_now", {"t1": int((now - 120.0) * 1000)}, now, now + 5),
            ("future_stamp_uses_now", {"t1": int((now + 10.0) * 1000)}, now, now + 5),
            ("no_stamp_uses_now", {"status": 201}, now, now + 5),
            ("non_dict_uses_now", "x", now, now + 5)):
        ex._fs_legacy_ctx = {"tcin": TCIN, "atc_ts": 0.0, "n": 0, "last_ts": 0.0}
        ex._fs_legacy_note_atc(atc)
        check(f"note_atc[{label}]", lo <= ex._fs_legacy_ctx["atc_ts"] <= hi, ex._fs_legacy_ctx)


def test_pe_impl_wiring():
    """The per-purchase context through the REAL _execute_purchase_impl (the
    stage-S2b harness: fast lane stubbed, legacy path stopped at checking_out)."""
    import test_won_cart_direct_smoke as W
    try:
        c = W.Clock()
        ex, _ = W.impl_ex(c, W.FL_PRE429)
        ex._fs_legacy_ctx = {"stale": True}
        W.run_impl(ex, {})
        check("impl_flag_off_ctx_none", ex._fs_legacy_ctx is None and "checking_out" in ex.statuses,
              (ex._fs_legacy_ctx, ex.statuses))
        ex, _ = W.impl_ex(c, W.FL_PRE429)
        t_before = time.time()
        W.run_impl(ex, {"TARGET_FS_TICKET_LOG": "1"})
        ctx = ex._fs_legacy_ctx
        check("impl_ctx_reset_and_atc_noted", isinstance(ctx, dict) and ctx.get("tcin") == W.TCIN
              and ctx.get("n") == 0 and ctx.get("last_ts") == 0.0
              and t_before <= ctx.get("atc_ts", 0) <= time.time()
              and "checking_out" in ex.statuses, (ctx, ex.statuses))
        fl0 = copy.deepcopy(W.FL_PRE429)
        t1 = int((time.time() - 0.7) * 1000)
        fl0["atc"]["t1"] = t1
        ex, _ = W.impl_ex(c, fl0)
        W.run_impl(ex, {"TARGET_FS_TICKET_LOG": "1"})
        check("impl_atc_ts_from_browser_stamp",
              abs((ex._fs_legacy_ctx or {}).get("atc_ts", 0) - t1 / 1000.0) < 1e-6, ex._fs_legacy_ctx)
        check("pin_impl_reset", "self._fs_legacy_ctx = ({'tcin': str(tcin), 'atc_ts': 0.0, 'n': 0, "
                                "'last_ts': 0.0}\n                               if fs_ticket_log_on() "
                                "else None)" in PE_SRC)
        check("pin_impl_atc_note_in_2xx_branch",
              "print(f\"[PURCHASE] Fetch ATC succeeded ({atc_status}), skipping cart signal wait\")\n"
              "                cart_confirmed = True\n"
              "                if getattr(self, '_fs_legacy_ctx', None) is not None:\n"
              "                    self._fs_legacy_note_atc(atc_result)" in PE_SRC)
    finally:
        shutil.rmtree(getattr(W, "_TMP", ""), ignore_errors=True)


# ═══════════════════════ RedSky pickup fields (SMON/SCR) ════════════════════

NEW_KEYS = {"pickup_status", "store_loc_id", "s_atp", "ship_atp"}
RAW = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _parse(raw, flag):
    with env(TARGET_REDSKY_STORE_OPTIONS_LOG=flag):
        res = StockMonitor()._process_response(copy.deepcopy(raw), 100)
    return {str(k): v for k, v in res.items()}


def _base(d):
    return {k: v for k, v in d.items() if k not in NEW_KEYS and k != "last_checked"}


def test_smon_fixture():
    off = _parse(RAW, None)
    zero = _parse(RAW, "0")
    on = _parse(RAW, "1")
    check("smon_same_tcins", set(off) == set(zero) == set(on) == {"21516452", "1011960739"}, (off, on))
    check("smon_off_no_new_keys", all(not (NEW_KEYS & set(v)) for v in list(off.values()) + list(zero.values())))
    check("smon_on_base_identical", all(_base(on[t]) == _base(off[t]) == _base(zero[t]) for t in off))
    check("smon_on_in_stock_max_qty", on["21516452"]["in_stock"] is True and on["1011960739"]["in_stock"] is False
          and on["21516452"]["max_qty"] == off["21516452"]["max_qty"]
          and on["1011960739"]["max_qty"] == off["1011960739"]["max_qty"])
    a, b = on["21516452"], on["1011960739"]
    check("smon_on_pickup_in_stock", (a["pickup_status"], a["store_loc_id"]) == ("IN_STOCK", "865"), a)
    check("smon_on_float_atp_kept", isinstance(a["s_atp"], float) and a["s_atp"] == 10.0
          and isinstance(a["ship_atp"], float) and a["ship_atp"] == 10.0, a)
    check("smon_on_pickup_unavailable", (b["pickup_status"], b["store_loc_id"], b["s_atp"], b["ship_atp"])
          == ("UNAVAILABLE", "865", 0.0, 0.0), b)
    check("smon_pin_after_entry", SMON_SRC.index("result[tcin] = {\n                    'title': clean_title,")
          < SMON_SRC.index("result[tcin].update(redsky_pickup_fields(fulfillment, shipping))"))


class _BoomDict(dict):
    def get(self, k, d=None):
        if k == "store_options":
            raise RuntimeError("boom")
        return super().get(k, d)


def test_smon_malformed():
    def mut(fn):
        raw = copy.deepcopy(RAW)
        fn(raw["data"]["product_summaries"][0]["fulfillment"])
        return raw

    def s0(f):
        return f["store_options"][0]

    cases = [
        ("empty_list", lambda f: f.__setitem__("store_options", []), ("", "", None, 10.0)),
        ("missing", lambda f: f.pop("store_options"), ("", "", None, 10.0)),
        ("string", lambda f: f.__setitem__("store_options", "junk"), ("", "", None, 10.0)),
        ("none_item", lambda f: f.__setitem__("store_options", [None]), ("", "", None, 10.0)),
        ("dict_not_list", lambda f: f.__setitem__("store_options", {"order_pickup": {"availability_status": "X"}}),
         ("", "", None, 10.0)),
        ("malformed_item", lambda f: f.__setitem__("store_options", [
            {"order_pickup": "x", "location_id": True, "location_available_to_promise_quantity": "NaN"}]),
         ("", "", None, 10.0)),
        ("nan_atp", lambda f: s0(f).__setitem__("location_available_to_promise_quantity", float("nan")),
         ("IN_STOCK", "865", None, 10.0)),
        ("int_atp", lambda f: s0(f).__setitem__("location_available_to_promise_quantity", 3),
         ("IN_STOCK", "865", 3, 10.0)),
        ("bool_atp", lambda f: s0(f).__setitem__("location_available_to_promise_quantity", True),
         ("IN_STOCK", "865", None, 10.0)),
        ("int_location", lambda f: s0(f).__setitem__("location_id", 865), ("IN_STOCK", "865", 10.0, 10.0)),
        ("ship_atp_string", lambda f: f["shipping_options"].__setitem__("available_to_promise_quantity", "10"),
         ("IN_STOCK", "865", 10.0, None)),
    ]
    for label, fn, want in cases:
        raw = mut(fn)
        off, on = _parse(raw, None), _parse(raw, "1")
        a = on.get("21516452") or {}
        got = (a.get("pickup_status"), a.get("store_loc_id"), a.get("s_atp"), a.get("ship_atp"))
        check(f"smon_malformed[{label}]", set(on) == set(off) == {"21516452", "1011960739"}
              and got == want and type(got[2]) is type(want[2])
              and _base(a) == _base(off["21516452"]), (got, want, a))
    raw = copy.deepcopy(RAW)
    ps = raw["data"]["product_summaries"][0]
    ps["fulfillment"] = _BoomDict(ps["fulfillment"])
    off = StockMonitor()._process_response(copy.deepcopy(raw), 1)
    with env(TARGET_REDSKY_STORE_OPTIONS_LOG="1"):
        on = StockMonitor()._process_response(raw, 1)
    on = {str(k): v for k, v in on.items()}
    off = {str(k): v for k, v in off.items()}
    check("smon_raising_store_options_keeps_tcin", "21516452" in on and not (NEW_KEYS & set(on["21516452"]))
          and _base(on["21516452"]) == _base(off["21516452"])
          and "pickup_status" in on["1011960739"], on)
    check("smon_pure_defaults", redsky_pickup_fields(None) == {"pickup_status": "", "store_loc_id": "",
                                                               "s_atp": None, "ship_atp": None}
          and redsky_pickup_fields({}, None)["ship_atp"] is None
          and redsky_pickup_fields({"shipping_options": {"available_to_promise_quantity": 4}})["ship_atp"] == 4)
    raw2 = copy.deepcopy(RAW)
    raw2["data"]["product_summaries"][1].pop("fulfillment")
    off2, on2 = _parse(raw2, None), _parse(raw2, "1")
    check("smon_no_fulfillment_same_as_off", set(on2) == set(off2)
          and _base(on2["1011960739"]) == _base(off2["1011960739"])
          and on2["1011960739"]["pickup_status"] == "" and on2["1011960739"]["ship_atp"] is None, on2)


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _checker():
    c = scr_mod.ResilientStockChecker.__new__(scr_mod.ResilientStockChecker)
    c._stock_monitor_parser = None
    c._tcin_status = {}
    c._last_seen_at = {}
    c._ever_seen_in_stock = set()
    c.on_in_stock = None
    c._test_mode_loop = False
    return c


def _ingest(c, raws, flag):
    h = _ListHandler()
    lg = scr_mod.logger
    old_level, old_prop = lg.level, lg.propagate
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False

    async def _go():
        c._status_lock = asyncio.Lock()
        for raw in raws:
            await c._ingest_bulk_response(SimpleNamespace(raw=copy.deepcopy(raw)))

    try:
        with env(TARGET_REDSKY_STORE_OPTIONS_LOG=flag):
            asyncio.run(_go())
    finally:
        lg.removeHandler(h)
        lg.setLevel(old_level)
        lg.propagate = old_prop
    return [m for m in h.records if m.startswith("[STOCK PICKUP]")]


def test_scr_ingest():
    c_off = _checker()
    recs = _ingest(c_off, [RAW], None)
    s = c_off._tcin_status
    check("scr_off_no_pickup", recs == [] and all(x.pickup == "" for x in s.values()) and len(s) == 2, recs)
    c_on = _checker()
    raw_flip = copy.deepcopy(RAW)
    raw_flip["data"]["product_summaries"][1]["fulfillment"]["store_options"][0]["order_pickup"] = {
        "availability_status": "IN_STOCK"}
    recs = _ingest(c_on, [RAW, RAW, raw_flip], "1")
    s2 = c_on._tcin_status
    check("scr_on_pickup_copied", s2["21516452"].pickup == "IN_STOCK" and s2["1011960739"].pickup == "IN_STOCK",
          {k: v.pickup for k, v in s2.items()})
    check("scr_on_transition_lines", len(recs) == 3
          and "[STOCK PICKUP] 21516452: pickup - -> IN_STOCK store=865 s_atp=10.0 ship=IN_STOCK "
              "ship_atp=10.0 in_stock=True" in recs
          and "[STOCK PICKUP] 1011960739: pickup - -> UNAVAILABLE store=865 s_atp=0.0 ship=OUT_OF_STOCK "
              "ship_atp=0.0 in_stock=False" in recs
          and recs[-1] == ("[STOCK PICKUP] 1011960739: pickup UNAVAILABLE -> IN_STOCK store=865 s_atp=0.0 "
                           "ship=OUT_OF_STOCK ship_atp=0.0 in_stock=False"), recs)
    same = all((s2[t].in_stock, s2[t].max_qty, s2[t].availability_status, s2[t].last_status_code)
               == (s[t].in_stock, s[t].max_qty, s[t].availability_status, s[t].last_status_code) for t in s)
    check("scr_on_stock_fields_identical", same and s2["21516452"].window_start_at > 0
          and c_on._ever_seen_in_stock == c_off._ever_seen_in_stock)
    # a pathological pickup value never stops the other TCINs from ingesting
    c_bad = _checker()

    class _BadStr:
        def __str__(self):
            raise RuntimeError("bad")

    c_bad._parse_bulk = lambda raw: {"A1": {"in_stock": True, "pickup_status": _BadStr(), "max_qty": 2},
                                     "B2": {"in_stock": True, "pickup_status": "LIMITED_STOCK", "max_qty": 1}}
    recs = _ingest(c_bad, [RAW], "1")
    sb = c_bad._tcin_status
    check("scr_bad_pickup_isolated", set(sb) == {"A1", "B2"} and sb["A1"].pickup == ""
          and sb["A1"].in_stock is True and sb["B2"].pickup == "LIMITED_STOCK" and len(recs) == 1, recs)
    st = scr_mod.TcinStatus(tcin="1", pickup="IN_STOCK")
    with env(TARGET_REDSKY_STORE_OPTIONS_LOG=None):
        check("scr_watch_suffix_off", scr_mod.stock_watch_pickup_suffix(st) == "")
    with env(TARGET_REDSKY_STORE_OPTIONS_LOG="1"):
        check("scr_watch_suffix_on", scr_mod.stock_watch_pickup_suffix(st) == " pickup=IN_STOCK"
              and scr_mod.stock_watch_pickup_suffix(scr_mod.TcinStatus(tcin="2")) == " pickup=-")
    check("scr_change_line_no_raise", scr_mod.pickup_change_line("1", None, None).startswith("[STOCK PICKUP] 1: pickup - -> -"))
    check("scr_pin_watch_suffix", 'f"last_clean_read={age:.0f}s ago"\n'
          "                            + stock_watch_pickup_suffix(s))" in SCR_SRC)


def main():
    tests = (test_ir_classify, test_ir_flags, test_ir_runs_and_counters, test_ir_census_format,
             test_mgr_exposure, test_r2_held_reentry_not_a_shot, test_mgr_census, test_mgr_source_pins,
             test_pe_pure_helpers, test_pe_chain_done_line, test_pe_js_insertion, test_pe_js_node,
             test_pe_interceptor, test_pe_stash_attribution, test_pe_legacy_fs_ticket,
             test_pe_note_atc, test_pe_impl_wiring,
             test_smon_fixture, test_smon_malformed, test_scr_ingest)
    for fn in tests:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAIL:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

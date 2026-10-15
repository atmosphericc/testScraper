#!/usr/bin/env python3
"""Offline tests: FX-1014-JOIN (2026-10-14, C-1014-13) -- free accounts join a race that is
still running on a live TCIN (TARGET_RACE_JOIN_FREE=1; default off = exact prior behaviour).

Drives the REAL race (_start_real_purchase -> racer threads -> _record_race_result) against
stub workers: one "rider" blocks (a won-cart ride) while the others finish, then
race_join_tick adds them back into the same race. The manager module's time.sleep is a
recording no-op. No browser, no network, no bot.

    python tests/test_race_join_1014.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time as _real_time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402
import test_mass_restock_1006 as MR  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm  # noqa: E402

check, env, quiet = H.check, H.env, H.quiet
A, B = MR.A, MR.B
EDGE = MR.EDGE
BASE = {**MR.FLAGS, **MR.RACE_ENV, **MR.MSKU, "TARGET_RETRY_WHILE_IN_STOCK_MAX": "1",
        "TARGET_STOCK_PROBE": "1", "TARGET_RACE_JOIN_FREE": "1", "TARGET_RACE_JOIN_GAP_S": "10"}
HELD = {"success": False, "reason": "won_cart_held", "error": "won-cart loop ended (budget_spent)"}


class _NoSleep:
    def __init__(self):
        self.rec = []

    def sleep(self, s):
        self.rec.append(float(s))

    def __getattr__(self, name):
        return getattr(_real_time, name)


class _RiderExec(H._Exec):
    """Blocks inside execute_purchase until released (a won cart being ridden)."""
    def __init__(self, script):
        super().__init__(script)
        self.release = threading.Event()

    async def execute_purchase(self, tcin, quantity=1):
        self.calls += 1
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return dict(HELD, tcin=tcin)


def _fleet():
    ws = [H._Worker(1, "primary", [HELD]), H._Worker(2, "business", [EDGE]), H._Worker(3, "alt-1", [EDGE])]
    ws[0].purchase_executor = _RiderExec([HELD])
    return ws


def _wait(pred, timeout=8.0):
    t0 = _real_time.time()
    while _real_time.time() - t0 < timeout:
        if pred():
            return True
        _real_time.sleep(0.02)
    return False


def _agg(m, tcin=A):
    return ((getattr(m, "_open_races", None) or {}).get(tcin) or {}).get("agg")


def _setup(extra=None, statuses=None):
    ws = _fleet()
    m = H.race_mgr(ws)
    MR._with_checker(m, statuses or {A: MR._st()})
    return ws, m


def test_join_flow():
    ws, m = _setup()
    saved = bpm.time
    bpm.time = _NoSleep()
    try:
        with env(**BASE):
            quiet(m._start_real_purchase, A, "Tin", {}, max_qty=1)
            ok = _wait(lambda: len(m.recorded) >= 2)
            check("j_two_finished_rider_alive", ok and ws[0].purchase_executor.calls == 1,
                  (len(m.recorded), [w.purchase_executor.calls for w in ws]))
            ag = _agg(m)
            check("j_open_race_registered", isinstance(ag, dict) and ag["total"] == 3, ag and ag.get("total"))
            check("j_row_attempting", (m._states.get(A) or {}).get("status") == "attempting", m._states.get(A))
            now = _real_time.time()
            joined = quiet(m.race_join_tick, now=now)[0]
            labels = sorted(l for _, l in joined)
            check("j_free_accounts_join", labels == ["W2/business", "W3/alt-1"], joined)
            check("j_rider_not_joined", all("primary" not in l for _, l in joined), joined)
            check("j_total_raised", ag["total"] == 5, ag["total"])
            ok = _wait(lambda: len(ag["results"]) >= 4)
            keys = sorted(ag["results"])
            check("j_joiners_recorded_own_keys", ok and any(k.startswith("W2/business#j") for k in keys)
                  and any(k.startswith("W3/alt-1#j") for k in keys)
                  and "W2/business" in keys and "W3/alt-1" in keys, keys)
            check("j_still_attempting_while_rider", (m._states.get(A) or {}).get("status") == "attempting",
                  m._states.get(A))
            check("j_gap_blocks_rejoin", quiet(m.race_join_tick)[0] == [])
            # age the join stamps past the 10 s gap and refresh the in-stock read (the
            # 5 s freshness rule reads real time)
            for _k in list(m._race_join_last):
                m._race_join_last[_k] -= 11.0
            m.stock_monitor._resilient_checker._tcin_status[A] = MR._st()
            again = quiet(m.race_join_tick)[0]
            check("j_rejoin_after_gap", sorted(l for _, l in again) == ["W2/business", "W3/alt-1"], again)
            _wait(lambda: len(ag["results"]) >= 6)
            ws[0].purchase_executor.release.set()
            ok = _wait(lambda: (m._states.get(A) or {}).get("status") == "failed")
            check("j_race_closes_after_all_report", ok and len(ag["results"]) == 7 and ag["total"] == 7,
                  (len(ag["results"]), ag["total"], m._states.get(A, {}).get("status")))
            check("j_open_race_cleared", A not in (getattr(m, "_open_races", None) or {}))
            check("j_no_join_after_close", quiet(m.race_join_tick, now=now + 30)[0] == [])
            check("j_reservations_released", _wait(lambda: m._worker_reservations == {}), m._worker_reservations)
    finally:
        ws[0].purchase_executor.release.set()
        bpm.time = saved


def _rider_running(extra_env=None, statuses=None):
    ws, m = _setup(statuses=statuses)
    saved = bpm.time
    bpm.time = _NoSleep()
    e = {**BASE, **(extra_env or {})}
    with env(**e):
        quiet(m._start_real_purchase, A, "Tin", {}, max_qty=1)
        _wait(lambda: len(m.recorded) >= 2)
    return ws, m, saved, e


def test_exclusions():
    # an account that ORDERED this TCIN never joins
    ws, m, saved, e = _rider_running()
    try:
        ws[1].purchase_executor._ordered_tcins = {A}
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("x_ordered_excluded", sorted(l for _, l in joined) == ["W3/alt-1"], joined)
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved
    # an account whose last result here was a sit-out never joins
    ws, m, saved, e = _rider_running()
    try:
        ag = _agg(m)
        with ag["lock"]:
            ag["results"]["W2/business"] = {"success": False, "reason": "account_parked_hot"}
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("x_sit_out_excluded", sorted(l for _, l in joined) == ["W3/alt-1"], joined)
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved
    # an account reserved for ANOTHER TCIN never joins
    ws, m, saved, e = _rider_running()
    try:
        with m._reserve_state():
            m._worker_reservations["W3/alt-1"] = (B, _real_time.time())
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("x_reserved_elsewhere_excluded", sorted(l for _, l in joined) == ["W2/business"], joined)
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved
    # another live TCIN that nobody races yet: no join (the fresh flip comes first)
    ws, m, saved, e = _rider_running(statuses={A: MR._st(), B: MR._st()})
    try:
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("x_unraced_live_tcin_blocks_join", joined == [], joined)
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved
    # the TCIN no longer reads live: no join
    ws, m, saved, e = _rider_running()
    try:
        m.stock_monitor._resilient_checker._tcin_status[A] = MR._st(in_stock=False)
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("x_not_live_no_join", joined == [], joined)
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved


def test_race_start_window():
    """Pre-arming verifier D1: between _start_real_purchase registering the race and the
    racer threads registering in _active_purchases (they block on _state_lock), the tick
    must NOT join the accounts whose original racers are about to fire."""
    ws, m = _setup()
    saved = bpm.time
    bpm.time = _NoSleep()
    try:
        with env(**BASE):
            with m._state_lock:
                quiet(m._start_real_purchase, A, "Tin", {}, max_qty=1)
                joined = quiet(m.race_join_tick)[0]
                ag = _agg(m)
                infl = set(ag.get("inflight") or ()) if ag else set()
            check("d1_no_join_before_racers_register", joined == [], joined)
            check("d1_inflight_recorded_at_start",
                  infl == {"W1/primary", "W2/business", "W3/alt-1"}, infl)
            _wait(lambda: len(m.recorded) >= 2)
            check("d1_finished_leave_inflight", set(ag["inflight"]) == {"W1/primary"}, ag["inflight"])
            joined = quiet(m.race_join_tick)[0]
            check("d1_then_free_accounts_join", sorted(l for _, l in joined) == ["W2/business", "W3/alt-1"], joined)
            check("d1_joiners_inflight", {"W2/business", "W3/alt-1"} <= set(ag["inflight"]), ag["inflight"])
    finally:
        ws[0].purchase_executor.release.set()
        bpm.time = saved


def test_freshness_and_budget():
    # the last in-stock read is 10 s old: still "live" for the probe, but too old to join
    ws, m, saved, e = _rider_running(statuses={A: MR._st(age=10.0, win_ago=12.0)})
    try:
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("f_stale_read_no_join", joined == [], joined)
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved
    check("b_budget_default", bpm._race_join_budget_s() == 30.0)
    with env(TARGET_RACE_JOIN_BUDGET_S="500"):
        check("b_budget_cap_110", bpm._race_join_budget_s() == 110.0)
    with env(TARGET_RACE_JOIN_BUDGET_S="1"):
        check("b_budget_floor_10", bpm._race_join_budget_s() == 10.0)
    check("f_fresh_default", bpm._race_join_fresh_s() == 5.0)
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("b_joiner_budget_wired", "_retry_budget_s = min(_retry_budget_s, _race_join_budget_s())" in src)


def test_flow_with_fx1006_flags():
    """The production FX-1006 + AC-1 flags on (the verifier noted BASE turns them off)."""
    fx = dict(TARGET_MULTI_SKU_FAIR_SHARE="1", TARGET_MULTI_SKU_MISS_REARM="1",
              TARGET_STOCK_RESET_KEEP_LIVE_FAILED="1", TARGET_WAVE_FIRST_YIELD_MISSED="1",
              TARGET_AMBIGUOUS_COMMIT_LATCH="1")
    ws, m, saved, e = _rider_running(fx)
    try:
        with env(**e):
            joined = quiet(m.race_join_tick)[0]
        check("fx_free_accounts_join", sorted(l for _, l in joined) == ["W2/business", "W3/alt-1"], joined)
        ag = _agg(m)
        ok = _wait(lambda: len(ag["results"]) >= 4)
        check("fx_joiners_report", ok, len(ag["results"]))
        ws[0].purchase_executor.release.set()
        ok = _wait(lambda: (m._states.get(A) or {}).get("status") == "failed")
        check("fx_race_closes", ok and len(ag["results"]) == ag["total"] == 5, (len(ag["results"]), ag["total"]))
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved


def test_flag_off():
    ws, m, saved, e = _rider_running({"TARGET_RACE_JOIN_FREE": None})
    try:
        check("off_no_open_race", not (getattr(m, "_open_races", None) or {}), getattr(m, "_open_races", None))
        with env(**{**e, "TARGET_RACE_JOIN_FREE": None}):
            check("off_tick_noop", m.race_join_tick() == [])
    finally:
        ws[0].purchase_executor.release.set(); bpm.time = saved
    check("cfg_gap_default", bpm._race_join_gap_s() == 10.0)
    with env(TARGET_RACE_JOIN_GAP_S="1"):
        check("cfg_gap_floor", bpm._race_join_gap_s() == 3.0)
    with env(TARGET_RACE_JOIN_GAP_S="junk"):
        check("cfg_gap_junk", bpm._race_join_gap_s() == 10.0)


def test_wiring():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    check("w_app_tick_hook", "_join = getattr(pm, 'race_join_tick', None)" in app)
    suite = (ROOT / "tests" / "run_offline_suite.py").read_text(encoding="utf-8")
    check("w_in_offline_suite", '"test_race_join_1014"' in suite)


def main():
    for t in (test_join_flow, test_exclusions, test_race_start_window, test_freshness_and_budget,
              test_flow_with_fx1006_flags, test_flag_off, test_wiring):
        try:
            t()
        except Exception as ex:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(ex).__name__}: {ex}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

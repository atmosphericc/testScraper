#!/usr/bin/env python3
"""Offline tests for the 2026-09-23 post-run arming (docs/CLAIMS.md C-0923-01..05).

A1  TARGET_WAVE_FIRST_EDGE=0 -- edge-429 / DCO-429 re-shots at the edge cadence
    (TARGET_ATC_EDGE429_RETRY_DELAY_MIN/MAX) instead of a 55-70 s cold re-entry;
    a 401 still takes the wave-first cold re-entry; the attempt cap still binds.
    No new code: this pins the flag's existing branch under the REAL race loop
    (tests/test_dco_burst.py hard-codes EDGE=1 everywhere, so the EDGE=0 branch
    had no race-loop test at all).
A3  TARGET_RETRY_STOP_WHEN_OOS=1 -- a race thread ends its window instead of
    firing a scheduled re-shot into stock the monitor has read out of stock for
    TARGET_RETRY_OOS_STOP_S (8). Fail-open on missing / stale monitor data.
G1  TARGET_STUCK_RESET_LIVE_GUARD=1 -- the 60 s stuck-purchase reset in
    process_stock_data skips a TCIN that still has a LIVE racer thread. Without
    it, a race still running past 60 s is reset to 'ready' and the next in-stock
    cycle opens a SECOND race on the same TCIN that re-claims the same accounts
    (reproduced below with the guard off).

Runs the REAL manager race loop against stub workers (no browser, no network),
re-using the harness from tests/test_identity_rest.py, and process_stock_data on
a manager built without __init__ (as tests/test_race_dispatch_smoke.py does).

Run: python tests/test_retry_oos_stop.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402  (harness: env, quiet, race_mgr, _Worker, check)
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet

T = H.HOT_TCIN
EDGE = {"success": False, "reason": "rate_limited_429", "gate_kind": "edge",
        "error": "ATC rate-limited (429)"}
A401 = {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401",
        "error": "ATC 401"}
WIN = {"success": True, "order_number": "OOS-1", "quantity": 1}

# The bat's cadence arming with the sleeps collapsed to the 1.0 s floor the edge
# cadence clamps to, a small attempt cap so the stub fleet finishes inside the
# harness's 20 s wait, and every other gate off.
BASE = dict(TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0",
            TARGET_ATC_EDGE429_RETRY_DELAY_MIN="1.0", TARGET_ATC_EDGE429_RETRY_DELAY_MAX="1.0",
            TARGET_RETRY_WARM="0", TARGET_WAVE_FIRST_ONLY="1", TARGET_WAVE_FIRST_EDGE="0",
            TARGET_SHOT_BANK_GATE="0", TARGET_401_PULSE="0", TARGET_RACE_ALL_WORKERS="1",
            TARGET_RETRY_WHILE_IN_STOCK="1", TARGET_RETRY_WHILE_IN_STOCK_MAX="3",
            TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="30", TARGET_ATC_DCO_AS_EDGE_CADENCE="1",
            TARGET_ATC_DCO_BURST="0", TARGET_IDENTITY_REST="0", TARGET_PARK_ACCOUNT_TCINS=None,
            TARGET_RETRY_STOP_WHEN_OOS="0", TARGET_RETRY_OOS_STOP_S=None,
            TARGET_STOCK_PROBE_FRESH_S=None, TARGET_MULTI_SKU_DISPATCH=None)


def _snap(last_true_ago, last_false_ago, age=0.2):
    """A stock_snapshot() stand-in: reads relative to the `now` the helper passes."""
    def fn(tcin, now=None):
        now = time.time() if now is None else now
        return {"live": None, "window_start": now - 60.0,
                "last_true": (now - last_true_ago) if last_true_ago is not None else 0.0,
                "last_false": (now - last_false_ago) if last_false_ago is not None else 0.0,
                "age": age}
    return fn


OOS_10S = _snap(last_true_ago=10.0, last_false_ago=0.2)      # gone 10 s, latest read False
IN_STOCK = _snap(last_true_ago=0.2, last_false_ago=5.0)      # latest read True
FLICKER = _snap(last_true_ago=2.0, last_false_ago=0.2)       # False now, but True 2 s ago
STALE = _snap(last_true_ago=40.0, last_false_ago=30.0, age=30.0)
NEVER_TRUE = _snap(last_true_ago=None, last_false_ago=0.2)


def _fleet(primary_script, others=(EDGE,)):
    return [H._Worker(1, "primary", primary_script), H._Worker(2, "business", list(others)),
            H._Worker(3, "alt-1", list(others))]


def _run(script, snap=None, **extra):
    ws = _fleet(script)
    m = H.race_mgr(ws)
    if snap is not None:
        m.stock_snapshot = snap
    with env(**{**BASE, **extra}):
        ok, out = quiet(H._race, m, T)
    return ok, out, ws, m


# ── 1. _retry_oos_gone_s unit ────────────────────────────────────────────────

class _M:
    def __init__(self, fn):
        self.stock_snapshot = fn


def test_helper_flag_off_is_none():
    with env(TARGET_RETRY_STOP_WHEN_OOS=None):
        check("h_off_default", bpm_mod._retry_oos_gone_s(_M(OOS_10S), T) is None)
    with env(TARGET_RETRY_STOP_WHEN_OOS="0"):
        check("h_off_zero", bpm_mod._retry_oos_gone_s(_M(OOS_10S), T) is None)


def test_helper_on():
    with env(TARGET_RETRY_STOP_WHEN_OOS="1", TARGET_RETRY_OOS_STOP_S=None,
             TARGET_STOCK_PROBE_FRESH_S=None):
        g = bpm_mod._retry_oos_gone_s(_M(OOS_10S), T)
        check("h_on_gone_10s", g is not None and 9.5 < g < 10.5, g)
        check("h_on_in_stock_none", bpm_mod._retry_oos_gone_s(_M(IN_STOCK), T) is None)
        check("h_on_flicker_under_8s_none", bpm_mod._retry_oos_gone_s(_M(FLICKER), T) is None)
        check("h_on_stale_none", bpm_mod._retry_oos_gone_s(_M(STALE), T) is None)
        check("h_on_never_true_none", bpm_mod._retry_oos_gone_s(_M(NEVER_TRUE), T) is None)
        check("h_on_never_false_none",
              bpm_mod._retry_oos_gone_s(_M(_snap(last_true_ago=30.0, last_false_ago=None)), T) is None)
        check("h_on_no_age_none",
              bpm_mod._retry_oos_gone_s(_M(lambda t, now=None: {"last_true": 1.0, "last_false": 2.0}), T) is None)

        def boom(t, now=None):
            raise RuntimeError("probe down")
        check("h_never_raises", bpm_mod._retry_oos_gone_s(_M(boom), T) is None)
        check("h_real_bare_mgr_fail_open", bpm_mod._retry_oos_gone_s(H.bare_mgr(), T) is None)


def test_helper_threshold_clamps():
    with env(TARGET_RETRY_STOP_WHEN_OOS="1", TARGET_RETRY_OOS_STOP_S="12"):
        check("h_thr_12_blocks_10s", bpm_mod._retry_oos_gone_s(_M(OOS_10S), T) is None)
    with env(TARGET_RETRY_STOP_WHEN_OOS="1", TARGET_RETRY_OOS_STOP_S="0.5"):   # clamps to 3
        check("h_thr_floor_3", bpm_mod._retry_oos_gone_s(_M(FLICKER), T) is None
              and bpm_mod._retry_oos_gone_s(_M(_snap(3.5, 0.2)), T) is not None)
    with env(TARGET_RETRY_STOP_WHEN_OOS="1", TARGET_RETRY_OOS_STOP_S="nan"):   # garbage -> 8
        check("h_thr_garbage_8", bpm_mod._retry_oos_gone_s(_M(_snap(7.0, 0.2)), T) is None
              and bpm_mod._retry_oos_gone_s(_M(_snap(8.5, 0.2)), T) is not None)


# ── 2. A3 in the real race loop ──────────────────────────────────────────────

def test_a3_off_fires_into_oos_as_before():
    ok, out, ws, m = _run([EDGE, EDGE, WIN], snap=OOS_10S)
    check("a3_off_finished", ok, m.recorded)
    check("a3_off_three_shots", ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("a3_off_no_line", "[RETRY_OOS]" not in out, out[-400:])


def test_a3_on_stops_before_the_sleep():
    ok, out, ws, m = _run([EDGE, EDGE, WIN], snap=OOS_10S, TARGET_RETRY_STOP_WHEN_OOS="1")
    check("a3_pre_finished", ok, m.recorded)
    check("a3_pre_one_shot", ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("a3_pre_all_accounts_stop", [w.purchase_executor.calls for w in ws] == [1, 1, 1],
          [w.purchase_executor.calls for w in ws])
    check("a3_pre_line", f"[RETRY_OOS] {T} no in-stock read for" in out
          and "no re-shot; ending the window after attempt 1" in out
          and "last_true_age=" in out, out[-600:])
    check("a3_pre_failed", m._states.get(T, {}).get("status") == "failed", m._states.get(T))


def test_a3_on_stops_when_stock_goes_during_the_sleep():
    calls = {"n": 0}

    def goes_away(tcin, now=None):
        calls["n"] += 1
        return (IN_STOCK if calls["n"] == 1 else OOS_10S)(tcin, now)
    ws = [H._Worker(1, "primary", [EDGE, EDGE, WIN])]
    m = H.race_mgr(ws)
    m.stock_snapshot = goes_away
    with env(**{**BASE, "TARGET_RETRY_STOP_WHEN_OOS": "1"}):
        ok, out = quiet(H._race, m, T)
    check("a3_mid_finished", ok, m.recorded)
    check("a3_mid_one_shot", ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("a3_mid_line", "not firing blind; keeping attempt 1's result" in out, out[-600:])


def test_a3_on_in_stock_fires_as_before():
    ok, out, ws, m = _run([EDGE, EDGE, WIN], snap=IN_STOCK, TARGET_RETRY_STOP_WHEN_OOS="1")
    check("a3_live_three_shots", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("a3_live_purchased", m._states.get(T, {}).get("status") == "purchased", m._states.get(T))
    check("a3_live_no_line", "[RETRY_OOS]" not in out, out[-400:])


def test_a3_on_fail_open():
    for name, snap in (("stale", STALE), ("never_true", NEVER_TRUE), ("flicker", FLICKER), ("none", None)):
        ok, out, ws, m = _run([EDGE, EDGE, WIN], snap=snap, TARGET_RETRY_STOP_WHEN_OOS="1")
        check(f"a3_failopen_{name}", ok and ws[0].purchase_executor.calls == 3 and "[RETRY_OOS]" not in out,
              (ws[0].purchase_executor.calls, out[-300:]))


# ── 3. A1: TARGET_WAVE_FIRST_EDGE=0 under the real race loop ────────────────

def test_a1_edge_reshoots_at_edge_cadence():
    t0 = time.time()
    ok, out, ws, m = _run([EDGE, EDGE, WIN], snap=IN_STOCK)
    dt = time.time() - t0
    check("a1_three_shots", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("a1_no_wave_first_on_edge", "[WAVE_FIRST] ATC-level edge on" not in out, out[-600:])
    check("a1_cadence_line", "[RETRY_CADENCE] edge-429 lottery cadence 1.0-1.0s" in out, out[-600:])
    check("a1_fast", dt < 15, dt)


def test_a1_control_edge1_takes_wave_first():
    ok, out, ws, m = _run([EDGE, WIN], snap=IN_STOCK, TARGET_WAVE_FIRST_EDGE="1",
                          TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="5")
    check("a1c_one_shot", ok and ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("a1c_wave_first_line", "[WAVE_FIRST] ATC-level edge on" in out and "ending the window" in out,
          out[-600:])


def test_a1_401_still_takes_wave_first():
    ok, out, ws, m = _run([A401, WIN], snap=IN_STOCK, TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="5")
    check("a1_401_one_shot", ok and ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("a1_401_wave_first", "[WAVE_FIRST] ATC-level auth401 on" in out and "ending the window" in out,
          out[-600:])


def test_a1_attempt_cap_binds():
    ok, out, ws, m = _run([EDGE], snap=IN_STOCK, TARGET_RETRY_WHILE_IN_STOCK_MAX="3")
    check("a1_cap_three", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("a1_cap_line", "Retry-while-in-stock budget spent (attempts=3" in out, out[-600:])


# ── 4. G1: the 60 s stuck reset never reopens a TCIN with a live racer ───────

class _Cfg:
    def __init__(self, wid, acct):
        self.worker_id = wid
        self.account_id = acct


class _PW:
    def __init__(self, wid, acct):
        self.cfg = _Cfg(wid, acct)

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"


class _Pool:
    def __init__(self, ws):
        self._w = list(ws)

    def ready_workers(self):
        return list(self._w)

    @property
    def primary(self):
        return self._w[0]

    @property
    def workers(self):
        return list(self._w)


MSKU = dict(TARGET_MULTI_SKU_DISPATCH="1", TARGET_MULTI_SKU_MAX_CONCURRENT="3",
            TARGET_MULTI_SKU_WORKERS_PER_TCIN="2", TARGET_MULTI_SKU_CAP_ALWAYS="1",
            TARGET_MULTI_SKU_RESERVE_TTL_S="120", TARGET_AMBIGUOUS_COMMIT_LATCH=None,
            TARGET_PARK_ACCOUNT_TCINS=None, TARGET_HOME_SHARE_GUARD=None,
            TARGET_IDENTITY_REST=None, TARGET_QTY_PER_TCIN=None,
            TARGET_RACE_STATE_STARTED_AT_GUARD=None)


def _dispatch_mgr(alive=True):
    """09-23's shape: a race on T by primary + business, 90 s old, both racer
    threads registered as '<T>#W1' / '<T>#W2'; alt-1 unreserved."""
    now = time.time()
    stop = threading.Event()
    th = threading.Thread(target=stop.wait, daemon=True)
    if alive:
        th.start()
    else:
        th.start()
        stop.set()
        th.join(2)
    m = object.__new__(BulletproofPurchaseManager)
    m._state_lock = threading.RLock()
    m._states = {T: {"status": "attempting", "started_at": now - 90, "real_purchase": True, "race": True}}
    m._load_states_unsafe = lambda: {k: dict(v) for k, v in m._states.items()}

    def _save(s):
        m._states = {k: dict(v) for k, v in s.items()}
        return True
    m._save_states_unsafe = _save
    m.status_callback = None
    m._active_purchases = {f"{T}#W1": {"thread": th, "started_at": now - 90, "status": "running"},
                           f"{T}#W2": {"thread": th, "started_at": now - 90, "status": "running"}}
    ws = [_PW(1, "primary"), _PW(2, "business"), _PW(3, "alt-1")]
    m.worker_pool = _Pool(ws)
    m._worker_reservations = {"W1/primary": (T, now - 90), "W2/business": (T, now - 90)}
    m._warmup_cycle_counter = 5
    m._maybe_run_session_sentinel = lambda: None
    m.started = []

    def _start(tcin, title, max_qty=1):
        m.started.append(tcin)
        return {"success": True, "duration": 1}
    m.start_purchase = _start
    return m, stop


STOCK = {T: {"in_stock": True, "title": "ETB", "max_qty": 2}}


def test_g1_off_reproduces_the_second_race():
    m, stop = _dispatch_mgr(alive=True)
    try:
        with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": None}):
            _, out = quiet(m.process_stock_data, STOCK)
    finally:
        stop.set()
    check("g1_off_force_reset", "Force-resetting stuck purchase" in out, out[-800:])
    check("g1_off_second_race_on_same_tcin", m.started == [T], (m.started, out[-800:]))
    check("g1_off_pursued_alongside_itself", f"pursuing alongside '{T}#W1'" in out, out[-800:])


def test_g1_on_blocks_the_second_race():
    m, stop = _dispatch_mgr(alive=True)
    try:
        with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": "1"}):
            _, out = quiet(m.process_stock_data, STOCK)
            _, out2 = quiet(m.process_stock_data, STOCK)     # a second in-stock cycle, same result
    finally:
        stop.set()
    check("g1_on_no_second_race", m.started == [], (m.started, out[-800:]))
    check("g1_on_no_force_reset", "Force-resetting stuck purchase" not in out + out2, out[-800:])
    check("g1_on_still_attempting", m._states[T]["status"] == "attempting", m._states[T])


def test_g1_on_dead_threads_still_recover():
    m, stop = _dispatch_mgr(alive=False)
    with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": "1"}):
        _, out = quiet(m.process_stock_data, STOCK)
    check("g1_dead_force_reset", "Force-resetting stuck purchase" in out, out[-800:])
    check("g1_dead_redispatched", m.started == [T], (m.started, out[-800:]))


def test_g1_production_call_order():
    """app._handle_stock_update calls reset_completed_purchases_by_stock_status
    (a SILENT 60 s 'attempting' -> 'ready' reset) BEFORE process_stock_data. This is
    the order the live bot runs; the adversarial review of the first cut showed the
    guard was bypassed because only the later copy was guarded."""
    m, stop = _dispatch_mgr(alive=True)
    try:
        with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": None}):
            _, out0 = quiet(m.reset_completed_purchases_by_stock_status, STOCK)
            _, out = quiet(m.process_stock_data, STOCK)
    finally:
        stop.set()
    check("g1_order_off_silent_reset", "Force-resetting stuck purchase" not in out0 + out, (out0 + out)[-600:])
    check("g1_order_off_second_race", m.started == [T], (m.started, out[-800:]))
    m, stop = _dispatch_mgr(alive=True)
    try:
        with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": "1"}):
            quiet(m.reset_completed_purchases_by_stock_status, STOCK)
            _, out = quiet(m.process_stock_data, STOCK)
    finally:
        stop.set()
    check("g1_order_on_no_second_race", m.started == [], (m.started, out[-800:]))
    check("g1_order_on_still_attempting", m._states[T]["status"] == "attempting", m._states[T])


def test_g1_silent_reset_guarded():
    for alive, flag, want in ((True, None, "ready"), (True, "1", "attempting"), (False, "1", "ready")):
        m, stop = _dispatch_mgr(alive=alive)
        try:
            with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": flag}):
                quiet(m.reset_completed_purchases_by_stock_status, STOCK)
        finally:
            stop.set()
        check(f"g1_silent_alive={alive}_flag={flag}", m._states[T]["status"] == want, m._states[T])


def test_g1_catch_all_at_dispatch():
    """Record already back at 'ready' (e.g. the 200 s force-complete + re-arm) while
    a racer is alive: the dispatch point itself refuses a second race."""
    for flag, want in ((None, [T]), ("1", [])):
        m, stop = _dispatch_mgr(alive=True)
        m._states = {T: {"status": "ready"}}
        try:
            with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": flag}):
                _, out = quiet(m.process_stock_data, STOCK)
        finally:
            stop.set()
        check(f"g1_catchall_flag={flag}", m.started == want, (m.started, out[-600:]))
        if flag:
            check("g1_catchall_line", f"Skipping {T} — a racer thread of its previous race is still alive" in out,
                  out[-600:])


def test_g1_scan_continues_past_a_live_race():
    """A live >60 s race no longer ends the scan: a genuinely dead record listed
    after it is still reset in the same cycle (it was, before the guard)."""
    m, stop = _dispatch_mgr(alive=True)
    dead = "95290385"
    m._states[dead] = {"status": "attempting", "started_at": time.time() - 90}
    try:
        with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": "1"}):
            quiet(m.process_stock_data, STOCK)            # the dead TCIN is out of stock
    finally:
        stop.set()
    check("g1_scan_dead_reset", m._states[dead] == {"status": "ready"}, m._states.get(dead))
    check("g1_scan_live_kept", m._states[T]["status"] == "attempting", m._states[T])
    check("g1_scan_no_dispatch", m.started == [], m.started)


def test_g1_reservation_sweep_keeps_live_racers():
    for alive, flag, kept in ((True, None, False), (True, "1", True), (False, "1", False)):
        m, stop = _dispatch_mgr(alive=alive)
        m._worker_reservations = {"W1/primary": (T, time.time() - 200)}
        try:
            with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": flag}):
                quiet(m._sweep_stale_reservations, 120)
        finally:
            stop.set()
        check(f"g1_sweep_alive={alive}_flag={flag}", ("W1/primary" in m._worker_reservations) == kept,
              m._worker_reservations)
    m, stop = _dispatch_mgr(alive=True)
    try:
        check("g1_resv_helper_live", m._reservation_has_live_racer("W1/primary", T) is True)
        check("g1_resv_helper_other_tcin", m._reservation_has_live_racer("W1/primary", "1010892067") is False)
        check("g1_resv_helper_unreserved_worker", m._reservation_has_live_racer("W3/alt-1", T) is False)
        check("g1_resv_helper_garbage", m._reservation_has_live_racer(None, T) is False)
    finally:
        stop.set()


U = "1010892067"
STOCK2 = {T: {"in_stock": True, "title": "ETB", "max_qty": 2},
          U: {"in_stock": True, "title": "Other", "max_qty": 2}}


def _degraded_mgr(register=True):
    """Only primary is session-ready and it is racing T through the legacy
    single-worker path (no reservation; registered under the bare TCIN) when U
    goes live -- the counter-path a second fresh-context review reproduced."""
    m, stop = _dispatch_mgr(alive=True)
    th = m._active_purchases[f"{T}#W1"]["thread"]
    m._states = {T: {"status": "attempting", "started_at": time.time() - 20, "real_purchase": True},
                 U: {"status": "ready"}}
    m._active_purchases = ({T: {"thread": th, "started_at": time.time() - 20, "status": "executing",
                                "worker": "W1/primary"}} if register else {})
    m.worker_pool = _Pool([_PW(1, "primary")])
    m._worker_reservations = {}
    return m, stop


def test_g1_degraded_fleet_never_hands_a_busy_account_to_a_second_tcin():
    for flag, want in ((None, [U]), ("1", [])):
        m, stop = _degraded_mgr()
        try:
            with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": flag}):
                _, out = quiet(m.process_stock_data, STOCK2)
        finally:
            stop.set()
        check(f"g1_degraded_flag={flag}", m.started == want, (m.started, out[-700:]))


def test_g1_degraded_fleet_before_the_thread_registers():
    """The legacy thread registers itself only once it runs: with <=1 ready worker
    a second TCIN is never pursued while a purchase is active, registered or not."""
    for flag, want in ((None, [U]), ("1", [])):
        m, stop = _degraded_mgr(register=False)
        try:
            with env(**{**MSKU, "TARGET_STUCK_RESET_LIVE_GUARD": flag}):
                _, out = quiet(m.process_stock_data, STOCK2)
        finally:
            stop.set()
        check(f"g1_degraded_unregistered_flag={flag}", m.started == want, (m.started, out[-700:]))


def test_g1_busy_worker_never_reserved_for_another_tcin():
    m, stop = _dispatch_mgr(alive=True)
    th = m._active_purchases[f"{T}#W1"]["thread"]
    m._active_purchases = {T: {"thread": th, "status": "executing", "worker": "W1/primary"}}
    m._worker_reservations = {}
    ws = m.worker_pool.ready_workers()
    try:
        with env(TARGET_STUCK_RESET_LIVE_GUARD="1"):
            check("g1_busy_free_excludes", [w.label() for w in m._free_workers(ws)] == ["W2/business", "W3/alt-1"])
            got = [w.label() for w in m._reserve_workers_for_tcin(U, ws, 3)]
            check("g1_busy_reserve_other_tcin_skips", got == ["W2/business", "W3/alt-1"], got)
            m._worker_reservations = {}
            got = [w.label() for w in m._reserve_workers_for_tcin(T, ws, 3)]
            check("g1_busy_reserve_same_tcin_allowed", got == ["W1/primary", "W2/business", "W3/alt-1"], got)
            check("g1_busy_map", m._busy_worker_tcins() == {"W1/primary": {T}}, m._busy_worker_tcins())
        m._worker_reservations = {}
        with env(TARGET_STUCK_RESET_LIVE_GUARD=None):
            check("g1_busy_off_free_includes", len(m._free_workers(ws)) == 3)
            got = [w.label() for w in m._reserve_workers_for_tcin(U, ws, 3)]
            check("g1_busy_off_reserve_takes_all", got == ["W1/primary", "W2/business", "W3/alt-1"], got)
    finally:
        stop.set()
    m._active_purchases = {"x": {"thread": None, "worker": "W1/primary"}, "y": None}
    check("g1_busy_map_tolerates_garbage", m._busy_worker_tcins() == {})


def test_a3_dco_result_is_exempt():
    """A DCO/FAST_SELLING 429 means Target's cart service just answered for the
    TCIN: the next shot fires even though the monitor reads OOS; the shot after an
    ordinary edge 429 does not."""
    DCO = {"success": False, "reason": "rate_limited_429", "gate_kind": "dco",
           "error": "ATC rate-limited (429)"}
    ws = [H._Worker(1, "primary", [DCO, EDGE, WIN])]
    m = H.race_mgr(ws)
    m.stock_snapshot = OOS_10S
    with env(**{**BASE, "TARGET_RETRY_STOP_WHEN_OOS": "1"}):
        ok, out = quiet(H._race, m, T)
    check("a3_dco_two_shots", ok and ws[0].purchase_executor.calls == 2, ws[0].purchase_executor.calls)
    check("a3_dco_then_stop", "no re-shot; ending the window after attempt 2" in out, out[-600:])


def test_g1_live_racer_helper():
    m, stop = _dispatch_mgr(alive=True)
    try:
        check("g1_h_live", m._tcin_has_live_racer(T) is True)
        check("g1_h_other_tcin", m._tcin_has_live_racer("1010892067") is False)
        check("g1_h_prefix_not_a_match", m._tcin_has_live_racer(T[:-1]) is False)
        m._active_purchases = {T + "1#W1": m._active_purchases[f"{T}#W1"]}
        check("g1_h_longer_tcin_not_a_match", m._tcin_has_live_racer(T) is False)
        m._active_purchases = {T: {"thread": m._active_purchases[T + "1#W1"]["thread"]}}
        check("g1_h_bare_key", m._tcin_has_live_racer(T) is True)
        m._active_purchases = {T: {"thread": None}}
        check("g1_h_no_thread", m._tcin_has_live_racer(T) is False)
        m._active_purchases = None
        check("g1_h_never_raises", m._tcin_has_live_racer(T) is False)
    finally:
        stop.set()


# ── 5. bat pins + source wiring ──────────────────────────────────────────────

def _bat_lines():
    return H.BAT_PATH.read_bytes().decode("utf-8", "replace").split("\r\n")


def test_bat_arming():
    lines = _bat_lines()
    for name, value in (("TARGET_WAVE_FIRST_EDGE", "0"),
                        ("TARGET_RETRY_STOP_WHEN_OOS", "1"),
                        ("TARGET_RETRY_OOS_STOP_S", "8"),
                        ("TARGET_STUCK_RESET_LIVE_GUARD", "1"),
                        ("TARGET_MULTI_SKU_WORKERS_PER_TCIN", "3")):
        check(f"bat_exact[{name}={value}]", lines.count(f"set {name}={value}") == 1)
        check(f"bat_last[{name}]", H._bat_value(name) == value, H._bat_value(name))
    # the edge cadence the A1 re-shots use is unchanged (2.0-3.0 s, armed 2026-09-01)
    check("bat_edge_cadence_min", H._bat_value("TARGET_ATC_EDGE429_RETRY_DELAY_MIN") == "2.0")
    check("bat_edge_cadence_max", H._bat_value("TARGET_ATC_EDGE429_RETRY_DELAY_MAX") == "3.0")
    # wave-first stays armed for 401s
    check("bat_wave_first_only_kept", H._bat_value("TARGET_WAVE_FIRST_ONLY") == "1")
    i = lines.index("set TARGET_RETRY_STOP_WHEN_OOS=1")
    rem = " ".join(lines[max(0, i - 30):i])
    check("bat_rem_explains", "C-0923" in rem and "Kill" in rem, rem[-300:])
    bad = [l for l in lines if l.startswith("REM") and "0923" in l
           and any(c in l for c in "%!|&<>^")]
    check("bat_rem_no_special_chars", not bad, bad[:3])


def test_source_wiring():
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("src_helper", "def _retry_oos_gone_s(mgr, tcin, now=None)" in src)
    check("src_two_call_sites", src.count("_retry_oos_gone_s(self, tcin)") == 2)
    check("src_top_of_loop_guarded",
          "if _attempt_n >= 2 and str((result or {}).get('gate_kind', '')) != 'dco':\n"
          "                            _oos_gone = _retry_oos_gone_s(self, tcin)" in src)
    check("src_guard_count", src.count("_stuck_reset_live_guard_on() and self._tcin_has_live_racer(tcin)") == 3
          and src.count("_stuck_reset_live_guard_on() and self._reservation_has_live_racer(lbl, tcin)") == 1
          and "if _pursue and _stuck_reset_live_guard_on() and len(_cands) <= 1:" in src
          and "busy = self._busy_worker_tcins() if _stuck_reset_live_guard_on() else {}" in src)
    s = src.find("def reset_completed_purchases_by_stock_status")
    check("src_silent_reset_guarded",
          0 < s < src.find("_stuck_reset_live_guard_on() and self._tcin_has_live_racer(tcin)", s)
          < src.find("def ", s + 10))
    i = src.find("if _attempt_n >= _retry_max or time.time() >= _retry_deadline:")
    j = src.find("no re-shot; ending the window")
    k = src.find("_wf_only = os.environ.get('TARGET_WAVE_FIRST_ONLY', '0') == '1'")
    check("src_pre_sleep_check_after_budget_before_cadence", 0 < i < j < k, (i, j, k))
    g = src.find("if elapsed > 60 and _stuck_reset_live_guard_on() and self._tcin_has_live_racer(tcin):")
    r = src.find("Force-resetting stuck purchase")
    check("src_guard_before_reset", 0 < g < r, (g, r))


def main():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            H.FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print("  FAIL:", f)
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

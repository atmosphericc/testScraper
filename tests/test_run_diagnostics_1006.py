#!/usr/bin/env python3
"""Offline tests for the 2026-10-06 run diagnostics (all log-only):

TARGET_STALL_WATCHDOG=1     app._stall_watchdog_tick: a core lock held past the wait or
                            a stuck stock sweep prints [STALL_WATCHDOG] STALL + every
                            thread's stack (throttled); [HEALTH] every health_s.
RESILIENT_OTHER_LOG=1       ResilientStockChecker._note_other: the 'other' bucket by
                            status / error / exit, one [STOCK][OTHER] line a minute.
RESILIENT_SUBNET_STEER_LOG=1  MultiSessionPool.pick_session: /16 steering at INFO,
                            once a minute per soured set.
FX-1006 decision lines      [FX1006_KEEP] / [MULTI_SKU_MISS_REARM] NOT hinted /
                            [FX1006] bought.
Bat pins for the three new flags.

Stubs and real module functions only: no browser, no network, no bot.
Run: python tests/test_run_diagnostics_1006.py
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_mass_restock_1006 as M   # noqa: E402  (manager harness + env/check)

check, env, quiet = M.check, M.env, M.quiet


def _out(fn, *a, **k):
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.msgs = []

    def emit(self, record):
        self.msgs.append(record.getMessage())


# ── 1. stall watchdog + health ───────────────────────────────────────────────

def test_watchdog_tick():
    import app
    pm = SimpleNamespace(_state_lock=threading.RLock(), _active_purchases={}, _worker_reservations={})
    chk = SimpleNamespace(_sweep_count=10)
    sm = SimpleNamespace(_resilient_checker=chk)
    state = {}
    st, out = _out(app._stall_watchdog_tick, pm, sm, state, wait_s=0.2, sweep_stall_s=0.5, health_s=0.0)
    check("wd_quiet_when_healthy", st == [] and "STALL" not in out, out)
    check("wd_health_line", "[HEALTH] rss=" in out and "chrome_procs_all=" in out and "disk_free=" in out, out)
    # a thread holds the EventBus lock -> STALL + every thread's stack
    held, release = threading.Event(), threading.Event()

    def _hold():
        with app.event_bus.lock:
            held.set()
            release.wait(5)
    th = threading.Thread(target=_hold, name="holder-thread", daemon=True)
    th.start()
    held.wait(2)
    try:
        st, out = _out(app._stall_watchdog_tick, pm, sm, state, wait_s=0.2, sweep_stall_s=60.0, health_s=999.0)
    finally:
        release.set()
        th.join(2)
    check("wd_bus_lock_stall", any("event_bus.lock" in x for x in st), st)
    check("wd_stall_line", "[STALL_WATCHDOG] STALL: event_bus.lock not free" in out, out[:400])
    check("wd_stack_dump_names_holder", "thread holder-thread" in out and "_hold" in out
          and "===== end of stacks =====" in out, out[-600:])
    frames = [ln for ln in out.splitlines() if ln.startswith("[STALL_WATCHDOG]   ")]
    check("wd_frame_text_debracketed", frames and not any("[" in ln[len("[STALL_WATCHDOG]   "):] for ln in frames),
          frames[:3])
    # dump throttled: an immediate second stall prints the STALL line but no second dump
    held.clear(); release.clear()
    th = threading.Thread(target=_hold, name="holder-thread", daemon=True)
    th.start()
    held.wait(2)
    try:
        st, out = _out(app._stall_watchdog_tick, pm, sm, state, wait_s=0.2, sweep_stall_s=60.0, health_s=999.0)
    finally:
        release.set()
        th.join(2)
    check("wd_dump_throttled", "STALL:" in out and "every thread's stack" not in out, out[:400])
    # sweep counter stuck
    state2 = {}
    _out(app._stall_watchdog_tick, pm, sm, state2, wait_s=0.2, sweep_stall_s=0.3, health_s=999.0)
    time.sleep(0.4)
    st, out = _out(app._stall_watchdog_tick, pm, sm, state2, wait_s=0.2, sweep_stall_s=0.3, health_s=999.0)
    check("wd_sweep_stuck", any("stock sweep stuck at #10" in x for x in st), st)
    chk._sweep_count = 11
    st, out = _out(app._stall_watchdog_tick, pm, sm, state2, wait_s=0.2, sweep_stall_s=0.3, health_s=999.0)
    check("wd_sweep_moving_quiet", not any("sweep" in x for x in st), st)
    # boot: the sweep counter is still 0 while the monitor starts its Chromes -> never a stall
    boot = SimpleNamespace(_resilient_checker=SimpleNamespace(_sweep_count=0))
    state3 = {}
    _out(app._stall_watchdog_tick, pm, boot, state3, wait_s=0.2, sweep_stall_s=0.3, health_s=999.0)
    time.sleep(0.4)
    st, out = _out(app._stall_watchdog_tick, pm, boot, state3, wait_s=0.2, sweep_stall_s=0.3, health_s=999.0)
    check("wd_no_false_stall_at_boot", st == [] and "STALL" not in out, (st, out[:300]))
    # never raises with nothing wired
    st, out = _out(app._stall_watchdog_tick, None, None, {}, wait_s=0.2, health_s=0.0)
    check("wd_no_pm_ok", isinstance(st, list) and "[HEALTH]" in out, out)
    check("wd_flag_default_off", (lambda: (os.environ.pop("TARGET_STALL_WATCHDOG", None), app._stall_watchdog_on())[1])()
          is False)


def test_watchdog_source_pins():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    seg = src[src.index("def _stall_watchdog_tick"):src.index("def _stall_watchdog_loop")]
    check("wd_locks_released_each", seg.count("lk.release()") == 1 and "acquire(timeout=wait_s)" in seg, seg[:300])
    check("wd_never_nests_locks", seg.count(".acquire(") == 1, seg.count(".acquire("))
    loop = src[src.index("def _stall_watchdog_loop"):src.index("# Global thread-safe data and event system")]
    check("wd_loop_swallows", "except Exception" in loop, loop)
    check("wd_started_only_on_flag", "if _stall_watchdog_on():" in src
          and "name='stall-watchdog'" in src)


# ── 2. monitor 'other' breakdown ─────────────────────────────────────────────

def _checker():
    from src.monitoring.stock_check_resilient import ResilientStockChecker
    return object.__new__(ResilientStockChecker)


def test_other_breakdown():
    import src.monitoring.stock_check_resilient as scr
    cap = _Cap()
    scr.logger.addHandler(cap)
    old_level = scr.logger.level
    scr.logger.setLevel(logging.INFO)
    try:
        with env(RESILIENT_OTHER_LOG="1"):
            c = _checker()
            c._split_429 = True
            for st, err, ip in ((206, "", "1.2.3.4"), (206, "", "1.2.3.4"), (0, "TimeoutError: read timed out", "5.6.7.8"),
                                (503, "", "1.2.3.4"), (429, "", "1.2.3.4")):
                c._note_other(SimpleNamespace(http_status=st, error=err, pinned_ip=ip))
            check("other_no_line_inside_minute", not any("[STOCK][OTHER]" in m for m in cap.msgs), cap.msgs)
            c._other_since -= 61.0
            c._note_other(SimpleNamespace(http_status=206, error="", pinned_ip="9.9.9.9"))
            line = next((m for m in cap.msgs if "[STOCK][OTHER]" in m), "")
            check("other_line_counts", "http=206x3" in line and "http=0/TimeoutErrorx1" in line
                  and "http=503x1" in line, line)
            check("other_line_by_exit", "by exit: 1.2.3.4x3" in line, line)
            check("other_split_429_not_counted", "http=429" not in line, line)
            check("other_resets_after_line", c._other_tally == {}, c._other_tally)
        with env(RESILIENT_OTHER_LOG=None):
            c = _checker()
            c._note_other(SimpleNamespace(http_status=206, error="", pinned_ip="1.1.1.1"))
            check("other_flag_off_no_state", getattr(c, "_other_tally", None) is None)
        c = _checker()
        with env(RESILIENT_OTHER_LOG="1"):
            c._note_other(None)   # garbage in -> never raises
        check("other_never_raises", True)
    finally:
        scr.logger.removeHandler(cap)
        scr.logger.setLevel(old_level)
    src = (ROOT / "src/monitoring/stock_check_resilient.py").read_text(encoding="utf-8")
    i = src.index("self._note_exit(result.pinned_ip, result.http_status)\n                try:")
    check("other_wired_in_non200_branch", "self._note_other(result)" in src[i:i + 200])


# ── 3. /16 steering at INFO ──────────────────────────────────────────────────

def _sess(ip, n4xx):
    now = time.time()
    return SimpleNamespace(state="ready", tab=object(), in_flight=False, cookies={"a": 1}, visitor_id="v",
                           captcha_parked_until=0.0, rate_parked_until=0.0, proxy_ip=ip,
                           recent_4xx=deque([now] * n4xx))


def test_subnet_steer_log():
    import src.session.multi_session_pool as msp
    cap = _Cap()
    msp.logger.addHandler(cap)
    old_level = msp.logger.level
    msp.logger.setLevel(logging.INFO)
    try:
        pool = object.__new__(msp.MultiSessionPool)
        pool.sessions = [_sess("168.158.1.1", msp.SUBNET_4XX_EXCLUDE_THRESHOLD), _sess("31.105.2.2", 0),
                         _sess("31.105.3.3", 0)]
        with env(RESILIENT_SUBNET_STEER_LOG="1"):
            for _ in range(5):
                pick = pool.pick_session()
            n = sum(1 for m in cap.msgs if "steering away from souring /16s" in m)
            check("steer_info_once_per_minute", n == 1, cap.msgs)
            pool.sessions.append(_sess("72.56.9.9", msp.SUBNET_4XX_EXCLUDE_THRESHOLD))   # a NEW soured set
            pool.pick_session()
            n2 = sum(1 for m in cap.msgs if "steering away from souring /16s" in m)
            check("steer_once_per_minute_overall", n2 == 1, cap.msgs)
            check("steer_line_names_subnet", any("'168.158'" in m and "1 of 3 ready" in m for m in cap.msgs), cap.msgs)
            check("steer_pick_unchanged", pick is not None and pick.proxy_ip.startswith("31.105"), pick)
        cap.msgs.clear()
        pool2 = object.__new__(msp.MultiSessionPool)
        pool2.sessions = [_sess("168.158.1.1", msp.SUBNET_4XX_EXCLUDE_THRESHOLD), _sess("31.105.2.2", 0)]
        with env(RESILIENT_SUBNET_STEER_LOG=None):
            pool2.pick_session()
        check("steer_flag_off_silent", not any("steering away" in m for m in cap.msgs), cap.msgs)
    finally:
        msp.logger.removeHandler(cap)
        msp.logger.setLevel(old_level)


# ── 4. FX-1006 decision lines ────────────────────────────────────────────────

def test_fx1006_decision_lines():
    A, B, C = M.A, M.B, M.C
    with env(**{**M.FLAGS, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": "1"}):
        m = M._mgr(states={A: {"status": "failed", "completed_at": time.time() - 60}, C: {"status": "ready"}},
                   statuses={A: M._st(win_ago=120), C: M._st()})
        _, out = quiet(m.reset_completed_purchases_by_stock_status, {C: {"in_stock": True}})
        check("line_keep_kept", f"[FX1006_KEEP] {A}: kept 'failed'" in out, out[-300:])
    with env(**{**M.FLAGS, **M.MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m, stop = M._miss_mgr()
        m._fx1006_note_purchase(B)
        try:
            _, out = quiet(m.process_stock_data, M.EV_B)
        finally:
            stop.set()
        check("line_not_hinted_bought", f"[MULTI_SKU_MISS_REARM] {B}: NOT hinted -- one of our accounts bought it"
              in out, out[-400:])
        m, stop = M._miss_mgr(b_status={"age": 100.0, "win_ago": 120.0})
        try:
            _, out = quiet(m.process_stock_data, M.EV_B)
        finally:
            stop.set()
        check("line_not_hinted_stale", f"[MULTI_SKU_MISS_REARM] {B}: NOT hinted -- latest read out of stock or too old"
              in out and "age=100s" in out, out[-400:])
        m = M._mgr(statuses={A: M._st()})
        _, out = quiet(m._fx1006_note_purchase, A)
        check("line_bought", f"[FX1006] {A}: bought" in out, out)
    with env(**M.FLAGS):
        m = M._mgr(statuses={A: M._st()})
        _, out = quiet(m._fx1006_note_purchase, A)
        check("line_bought_flags_off_silent", "[FX1006]" not in out, out)


# ── 5. bat pins ──────────────────────────────────────────────────────────────

def test_bat_pins():
    raw = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes()
    check("bat_crlf_ascii", raw.count(b"\r\n") == raw.count(b"\n") and raw.isascii())
    txt = raw.decode("ascii")
    for name in ("TARGET_STALL_WATCHDOG", "RESILIENT_OTHER_LOG", "RESILIENT_SUBNET_STEER_LOG"):
        hits = re.findall(rf"^set {name}=(\S*)\r?$", txt, re.M)
        check(f"bat_{name}_set_once_to_1", hits == ["1"], hits)


def main():
    for t in (test_watchdog_tick, test_watchdog_source_pins, test_other_breakdown, test_subnet_steer_log,
              test_fx1006_decision_lines, test_bat_pins):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(e).__name__}: {e}")
    print(f"\n=== {len(M.H.PASSED)}/{len(M.H.PASSED) + len(M.H.FAILED)} passed ===")
    for f in M.H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not M.H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

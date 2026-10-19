#!/usr/bin/env python3
"""Offline tests for F1 (2026-10-18) TARGET_THREAD_START_GUARD.

10-07: an AttributeError in StockMonitorThread.start() stopped monitoring_loop before
purchase_thread.start(): process alive, unable to buy. With TARGET_THREAD_START_GUARD=1
the purchase thread is started FIRST and each start() has its own try/except; default
(flag unset/0) keeps the old sequential unguarded code path (stock first, no guard).

The REAL monitoring_loop / helper source is lifted out of app.py with ast and executed
against stub threads (importing app.py would pull in the whole bot). No browser, no network.

v2 (2026-10-19): the Opus pre-arming verifier refuted "retries on the SAME object, never builds a
new one" and "gives up after a bounded number of attempts". The real start() methods set
self.thread BEFORE Thread.start(), so a Thread.start() that raises leaves a Thread object that
exists but is not alive; v1 then took the "died" branch and built new objects forever. v2
distinguishes "never became alive" (retry the SAME object, max 3, then abandon) from "was alive
and died" (replacement capped at 3 per kind per process, never while an earlier object is alive).

Run: python tests/test_thread_start_guard_1018.py
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
FUNCS = ("_thread_start_guard_on", "_start_buy_threads_guarded",
         "_health_check_guarded", "monitoring_loop",
         "_tsg_thread_alive", "_tsg_thread_ran", "_tsg_mark_alive_once", "_tsg_log_traceback",
         "_tsg_abandon", "_tsg_start_attempt", "_tsg_health_one")

_fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def _load_funcs(only=None):
    tree = ast.parse(APP)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCS]
    assert {n.name for n in nodes} == set(FUNCS), [n.name for n in nodes]
    if only:
        nodes = [n for n in nodes if n.name == only]
    return compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec")


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Bus:
    """Fake EventBus: records every subscription, like the real one (app.py EventBus.subscribe)."""

    def __init__(self):
        self.subs = []

    def subscribe(self, event, cb):
        self.subs.append((event, cb))


class _Base:
    """Stub of StockMonitorThread / PurchaseManagerThread."""
    log = None
    fail_start = False      # True / "early": raise before self.thread exists
    fail_mode = None        # "late": raise AFTER the worker thread started (the real 10-07 shape)
    fail_times = None       # None = always fail; N = fail the first N start() calls then succeed
    fail_after = 0          # the first N start() calls succeed, later ones fail (a thread that died, then
                            # its replacement cannot start)
    short_lived = False     # worker thread exits at once (a started thread that has since died)
    made = None
    starts = None
    SUBSCRIBES = False

    def __init__(self, *a, **kw):
        # the real PurchaseManagerThread.__init__ subscribes _handle_stock_update to
        # 'stock_updated' on the bus and nothing ever unsubscribes (app.py:1716), so every object
        # built stays subscribed for the life of the process
        if self.SUBSCRIBES and a:
            a[0].subscribe('stock_updated', self._handle_stock_update)
        self.thread = None
        self.stock_monitor = object()
        self.stopped = False
        type(self).made.append(self)

    def start(self):
        cls = type(self)
        self.log.append(self.NAME)
        cls.starts += 1
        failing = ((cls.fail_start or cls.fail_mode) and cls.starts > cls.fail_after
                   and (cls.fail_times is None or cls.starts <= cls.fail_times))
        if failing and not cls.fail_mode:
            raise AttributeError(f"{self.NAME} diag boom")
        self.running = True
        # like the real start() methods (app.py ~1193 / ~1797): self.thread is assigned BEFORE
        # Thread.start(), so a Thread.start() that raises leaves a Thread that exists, is not alive
        self.thread = threading.Thread(target=(lambda: None) if cls.short_lived else time.sleep,
                                       args=() if cls.short_lived else (30,), daemon=True)
        if failing and cls.fail_mode == "unstarted":
            raise RuntimeError("can't start new thread")
        self.thread.start()
        if cls.short_lived:
            self.thread.join()  # deterministic: the worker is gone before start() returns / raises
        if failing and cls.fail_mode == "late":
            raise AttributeError(f"{self.NAME} diag boom")

    def _handle_stock_update(self, *a, **k):
        pass

    def set_stock_monitor(self, sm):
        pass

    def stop(self):
        self.stopped = True


def _run_loop(flag, stock_fails=False, purchase_fails=False, cycles=2, stock_fail_times=None,
              stock_short_lived=False, purchase_short_lived=False, purchase_fail_times=None,
              stock_fail_after=0, purchase_fail_after=0):
    """Execute the real monitoring_loop with stubs."""
    log = []
    bus = _Bus()

    class Stock(_Base):
        NAME = "stock"

    class Purchase(_Base):
        NAME = "purchase"
        SUBSCRIBES = True

    for cls, fails in ((Stock, stock_fails), (Purchase, purchase_fails)):
        cls.log = log
        cls.fail_mode = fails if fails in ("late", "unstarted") else None
        cls.fail_start = bool(fails) and fails not in ("late", "unstarted")
        cls.fail_times = None
        cls.fail_after = 0
        cls.short_lived = False
        cls.made = []
        cls.starts = 0
    Stock.fail_times = stock_fail_times
    Stock.fail_after = stock_fail_after
    Purchase.fail_after = purchase_fail_after
    Stock.short_lived = stock_short_lived
    Purchase.fail_times = purchase_fail_times
    Purchase.short_lived = purchase_short_lived

    shared = SimpleNamespace(lock=_Lock(), monitor_running=False)
    sleeps = {"n": 0}

    def fake_sleep(s):
        sleeps["n"] += 1
        if sleeps["n"] >= cycles:  # let `cycles` health cycles run, then stop the loop
            shared.monitor_running = False

    ns = {
        "os": os, "time": SimpleNamespace(sleep=fake_sleep, time=time.time), "threading": threading,
        "StockMonitorThread": Stock, "PurchaseManagerThread": Purchase,
        "event_bus": bus, "shared_data": shared,
        "global_purchase_manager": None, "global_event_loop": None,
        "add_activity_log": lambda *a, **k: None,
    }
    exec(_load_funcs(), ns)
    buf = io.StringIO()
    old = os.environ.get("TARGET_THREAD_START_GUARD")
    if flag is None:
        os.environ.pop("TARGET_THREAD_START_GUARD", None)
    else:
        os.environ["TARGET_THREAD_START_GUARD"] = flag
    err = None
    try:
        with contextlib.redirect_stdout(buf):
            try:
                ns["monitoring_loop"]()
            except BaseException as e:  # the default path is allowed to raise
                err = e
    finally:
        if old is None:
            os.environ.pop("TARGET_THREAD_START_GUARD", None)
        else:
            os.environ["TARGET_THREAD_START_GUARD"] = old
    Purchase.bus = bus
    return log, buf.getvalue(), err, Stock, Purchase


def main():
    # ---- flag default / parsing -------------------------------------------------
    g = {"os": os}
    exec(_load_funcs("_thread_start_guard_on"), g)
    old = os.environ.pop("TARGET_THREAD_START_GUARD", None)
    check("flag unset -> off", g["_thread_start_guard_on"]() is False)
    for v, want in (("0", False), ("", False), ("1", True), ("true", True), (" 1 ", True), ("no", False)):
        os.environ["TARGET_THREAD_START_GUARD"] = v
        check(f"flag {v!r} -> {want}", g["_thread_start_guard_on"]() is want)
    os.environ.pop("TARGET_THREAD_START_GUARD", None)
    if old is not None:
        os.environ["TARGET_THREAD_START_GUARD"] = old

    # ---- OFF, healthy: old ordering (stock first, then purchase) ----------------
    log, out, err, S, P = _run_loop(None)
    check("OFF healthy: no error", err is None, repr(err))
    check("OFF healthy: old order stock then purchase", log[:2] == ["stock", "purchase"], str(log))
    check("OFF healthy: old log markers",
          "Starting stock_thread..." in out and "[OK] purchase_thread started" in out)
    check("OFF healthy: no new-code markers", "start failed" not in out and "NOT all threads" not in out)
    check("OFF healthy: banner unchanged", "All threads started successfully" in out)

    # ---- OFF (explicit 0), stock start raises: today's behaviour = purchase never started
    log, out, err, S, P = _run_loop("0", stock_fails=True)
    check("OFF + stock raises: exception escapes (old behaviour)", isinstance(err, AttributeError), repr(err))
    check("OFF + stock raises: purchase_thread never started", log == ["stock"], str(log))

    # ---- ON, healthy: purchase FIRST, both alive --------------------------------
    log, out, err, S, P = _run_loop("1")
    check("ON healthy: no error", err is None, repr(err))
    check("ON healthy: purchase then stock", log[:2] == ["purchase", "stock"], str(log))
    check("ON healthy: both started markers",
          "[OK] purchase_thread started (is_alive: True)" in out
          and "[OK] stock_thread started (is_alive: True)" in out)
    check("ON healthy: no [ERROR]", "[ERROR]" not in out, out[-300:])
    check("ON healthy: banner says all threads started", "All threads started successfully" in out)
    check("ON healthy: no restarts", len(S.made) == 1 and len(P.made) == 1, f"{len(S.made)}/{len(P.made)}")

    # ---- ON, stock start raises: purchase alive, error line, process alive -------
    log, out, err, S, P = _run_loop("1", stock_fails=True)
    check("ON + stock raises: loop did not raise", err is None, repr(err))
    check("ON + stock raises: purchase started first", log[0] == "purchase", str(log))
    check("ON + stock raises: purchase_thread started + alive marker",
          "[OK] purchase_thread started (is_alive: True)" in out)
    check("ON + stock raises: [MONITORING_LOOP] [ERROR] stock_thread start failed",
          "[MONITORING_LOOP] [ERROR] stock_thread start failed: AttributeError('stock diag boom')" in out,
          out[-400:])
    check("ON + stock raises: purchase thread alive and NOT replaced",
          len(P.made) == 1 and P.made[0].thread is not None and P.made[0].thread.is_alive(),
          str(len(P.made)))
    check("ON + stock raises: purchase thread stopped only by the normal shutdown finally",
          P.made[0].stopped is True)  # stopped only by the normal finally at shutdown
    check("ON + stock raises (early): NO new StockMonitorThread built (retry on the same object)",
          len(S.made) == 1, str(len(S.made)))
    check("ON + stock raises (early): purchase thread never rebuilt", len(P.made) == 1, str(len(P.made)))

    # ---- ON, stock start raises BEFORE its thread exists, 6 health cycles: exact cap ----------
    log, out, err, S, P = _run_loop("1", stock_fails=True, cycles=6)
    check("ON + stock early raise x6 cycles: loop did not raise", err is None, repr(err))
    check("ON + stock early raise x6 cycles: still exactly one StockMonitorThread",
          len(S.made) == 1, str(len(S.made)))
    check("ON + stock early raise x6 cycles: start() called exactly 1 + 3 (cap) times",
          S.starts == 4, str(S.starts))
    check("ON + stock early raise x6 cycles: retry lines 1/3 2/3 3/3, no 4th, abandoned once",
          all(f"stock_thread start retry {i}/3 failed" in out for i in (1, 2, 3))
          and "retry 4/3" not in out
          and out.count("stock_thread start abandoned after 3 attempts") == 1, out[-600:])
    check("ON + stock early raise x6 cycles: purchase alive and untouched",
          len(P.made) == 1 and P.starts == 1 and P.made[0].thread.is_alive(), f"{len(P.made)}/{P.starts}")

    # ---- ON, stock start raises once then recovers: same object, fails reset ------------------
    log, out, err, S, P = _run_loop("1", stock_fails=True, cycles=4, stock_fail_times=2)
    check("ON + stock recovers on retry: no error", err is None, repr(err))
    check("ON + stock recovers on retry: same object, 3 start() calls, thread alive",
          len(S.made) == 1 and S.starts == 3 and S.made[0].thread is not None
          and S.made[0].thread.is_alive(), f"{len(S.made)}/{S.starts}")

    # ---- ON, the real 10-07 shape: start() raises AFTER self.thread.start() -------------------
    log, out, err, S, P = _run_loop("1", stock_fails="late", cycles=4)
    check("ON + stock late raise: loop did not raise", err is None, repr(err))
    check("ON + stock late raise: purchase started first and alive",
          log[0] == "purchase" and P.made[0].thread.is_alive() and "[OK] purchase_thread started" in out)
    check("ON + stock late raise: [ERROR] line printed",
          "[MONITORING_LOOP] [ERROR] stock_thread start failed: AttributeError('stock diag boom')" in out,
          out[-400:])
    check("ON + stock late raise: NO reconstruction, start() called once",
          len(S.made) == 1 and S.starts == 1 and len(P.made) == 1, f"{len(S.made)}/{S.starts}/{len(P.made)}")

    # ---- ON, a started stock thread that later died: rebuilt (the flag-off behaviour) ----------
    log, out, err, S, P = _run_loop("1", cycles=2, stock_short_lived=True)
    check("ON + started stock thread died: rebuilt via the dead-thread path",
          err is None and len(S.made) >= 2 and "Stock thread died, restarting" in out,
          f"{err!r} {len(S.made)}")

    # ---- ON, purchase start raises: stock still started, error line --------------
    log, out, err, S, P = _run_loop("1", purchase_fails=True)
    check("ON + purchase raises: loop did not raise", err is None, repr(err))
    check("ON + purchase raises: stock still started", "stock" in log)
    check("ON + purchase raises: error line names purchase_thread",
          "[MONITORING_LOOP] [ERROR] purchase_thread start failed: "
          "AttributeError('purchase diag boom')" in out)
    check("ON + purchase raises: banner does NOT claim all threads started",
          "All threads started successfully" not in out and "NOT all threads started" in out, out[-500:])

    # ---- ON, purchase start() always fails (early) over 10 cycles: ONE object, ONE subscriber,
    # ---- exactly 1 + 3 start() calls, then abandoned (v2: bounded, was 1 + one per cycle forever)
    log, out, err, S, P = _run_loop("1", purchase_fails=True, cycles=10)
    check("ON + purchase early raise x10 cycles: loop did not raise", err is None, repr(err))
    check("ON + purchase early raise x10 cycles: exactly one PurchaseManagerThread built",
          len(P.made) == 1, str(len(P.made)))
    subs = [cb for (ev, cb) in P.bus.subs if ev == 'stock_updated']
    check("ON + purchase early raise x10 cycles: exactly one stock_updated subscriber",
          len(subs) == 1, str(len(subs)))
    check("ON + purchase early raise x10 cycles: start() called 1 + 3 retries, then abandoned",
          P.starts == 1 + 3, str(P.starts))
    check("ON + purchase early raise x10 cycles: retries use the same object, never 'died, restarting'",
          "Purchase thread never started, retrying" in out and "Purchase thread died" not in out, out[-500:])
    check("ON + purchase early raise x10 cycles: stock thread untouched",
          len(S.made) == 1 and S.starts == 1 and S.made[0].thread.is_alive(), f"{len(S.made)}/{S.starts}")
    check("ON + purchase early raise x10 cycles: retry lines 1/3 2/3 3/3 and ONE abandoned line",
          all(f"purchase_thread start retry {i}/3 failed" in out for i in (1, 2, 3))
          and out.count("purchase_thread start abandoned after 3 attempts") == 1, out[-700:])

    # ---- ON, purchase always fails over 65 cycles: still bounded --------------------------------
    log, out, err, S, P = _run_loop("1", purchase_fails=True, cycles=65)
    check("ON + purchase early raise x65 cycles: one object, one subscriber, 1 + 3 starts (no 66)",
          len(P.made) == 1 and len([1 for (ev, cb) in P.bus.subs if ev == 'stock_updated']) == 1
          and P.starts == 4, f"{len(P.made)}/{P.starts}")
    check("ON + purchase early raise x65 cycles: log not flooded (<= 8 ERROR lines)",
          out.count("[ERROR]") <= 8, str(out.count("[ERROR]")))

    # ---- ON, purchase fails twice then recovers: same object, becomes alive -------------------
    log, out, err, S, P = _run_loop("1", purchase_fails=True, cycles=5, purchase_fail_times=2)
    check("ON + purchase recovers on retry: same object, 3 start() calls, alive, one subscriber",
          err is None and len(P.made) == 1 and P.starts == 3 and P.made[0].thread is not None
          and P.made[0].thread.is_alive()
          and len([1 for (ev, cb) in P.bus.subs if ev == 'stock_updated']) == 1,
          f"{err!r} {len(P.made)}/{P.starts}")

    # ---- ON, a started purchase thread that later died: rebuilt (flag-off behaviour) ----------
    log, out, err, S, P = _run_loop("1", cycles=2, purchase_short_lived=True)
    check("ON + started purchase thread died: rebuilt via the dead-thread path",
          err is None and len(P.made) >= 2 and "Purchase thread died, restarting" in out,
          f"{err!r} {len(P.made)}")

    # ================= v2: the realistic shape (self.thread set, Thread.start() raised) ==========
    for kind in ("stock", "purchase"):
        kw = {f"{kind}_fails": "unstarted"}
        log, out, err, S, P = _run_loop("1", cycles=12, **kw)
        K, O = (S, P) if kind == "stock" else (P, S)
        check(f"v2 {kind} unstarted Thread x12 cycles: loop did not raise", err is None, repr(err))
        check(f"v2 {kind} unstarted Thread: NO new object built (first never became alive)",
              len(K.made) == 1, str(len(K.made)))
        check(f"v2 {kind} unstarted Thread: the Thread exists but is not alive (precondition)",
              K.made[0].thread is not None and not K.made[0].thread.is_alive())
        check(f"v2 {kind} unstarted Thread: start() 1 + exactly 3 retries on the same object",
              K.starts == 4, str(K.starts))
        check(f"v2 {kind} unstarted Thread: retry lines 1/3, 2/3, 3/3 with the exception repr",
              all(f"{kind}_thread start retry {i}/3 failed: RuntimeError(\"can't start new thread\")" in out
                  for i in (1, 2, 3)), out[-700:])
        check(f"v2 {kind} unstarted Thread: abandoned line exactly once, no 4th retry",
              out.count(f"{kind}_thread start abandoned after 3 attempts") == 1
              and "retry 4/3" not in out, out[-500:])
        check(f"v2 {kind} unstarted Thread: never took the 'died' branch",
              "thread died" not in out)
        check(f"v2 {kind} unstarted Thread: [OK] {kind}_thread started marker never printed (RED)",
              f"[OK] {kind}_thread started" not in out)
        check(f"v2 {kind} unstarted Thread: the other thread untouched and alive",
              len(O.made) == 1 and O.starts == 1 and O.made[0].thread.is_alive(), f"{len(O.made)}/{O.starts}")
    log, out, err, S, P = _run_loop("1", cycles=12, purchase_fails="unstarted")
    check("v2 purchase unstarted: still ONE stock_updated subscriber (no leaked objects)",
          len([1 for (ev, cb) in P.bus.subs if ev == 'stock_updated']) == 1)

    # recovery inside the budget: fails twice, third retry works -> same object, alive, no abandon
    log, out, err, S, P = _run_loop("1", cycles=8, purchase_fails="unstarted", purchase_fail_times=3)
    check("v2 purchase unstarted x3 then ok: same object, 4 starts, alive, not abandoned",
          len(P.made) == 1 and P.starts == 4 and P.made[0].thread.is_alive()
          and "abandoned" not in out, f"{len(P.made)}/{P.starts}")
    check("v2 purchase recovered: ran through the normal alive state afterwards (no 'died')",
          "thread died" not in out)

    # a Thread that was started once and is gone (never observed alive): never a 2nd Thread.start()
    log, out, err, S, P = _run_loop("1", cycles=6, stock_fails="late", stock_short_lived=True)
    check("v2 stock late raise + worker already gone: abandoned, NOT retried, NOT rebuilt",
          err is None and len(S.made) == 1 and S.starts == 1
          and out.count("stock_thread start abandoned after 0 attempts") == 1
          and "already started once" in out, f"{err!r} {len(S.made)}/{S.starts}\n{out[-600:]}")

    # traceback: once per distinct failure site (the stub raises from ONE line for every start)
    check("v2 traceback printed exactly once per distinct site, truncated",
          out.count("start failure traceback") == 1 and "Traceback (most recent call last)" in out)
    tb_block = out.split("start failure traceback", 1)[1]
    check("v2 traceback block <= ~1500 chars of traceback text",
          len(tb_block.split("\n[MONITORING_LOOP]", 1)[0].split("\n", 1)[1]) <= 1500)

    # ---- v2: a thread that WAS alive then died: replacements capped at 3 ------------------------
    for kind in ("stock", "purchase"):
        kw = {f"{kind}_short_lived": True}
        log, out, err, S, P = _run_loop("1", cycles=12, **kw)
        K, O = (S, P) if kind == "stock" else (P, S)
        check(f"v2 {kind} started-then-died x12 cycles: loop did not raise", err is None, repr(err))
        check(f"v2 {kind} started-then-died: replacements capped (1 original + 3)",
              len(K.made) == 4, str(len(K.made)))
        check(f"v2 {kind} started-then-died: each replacement was logged, then ONE cap line",
              all(f"(replacement {i}/3)" in out for i in (1, 2, 3)) and "(replacement 4/3)" not in out
              and out.count(f"{kind}_thread replacement cap reached") == 1, out[-800:])
        check(f"v2 {kind} started-then-died: no never-started retries (it did run)",
              "never started, retrying" not in out)
        check(f"v2 {kind} started-then-died: the other kind untouched",
              len(O.made) == 1 and O.starts == 1, f"{len(O.made)}/{O.starts}")
    log, out, err, S, P = _run_loop("1", cycles=12, purchase_short_lived=True)
    check("v2 purchase died x12: at most 1 + 3 stock_updated subscribers (leak bounded)",
          len([1 for (ev, cb) in P.bus.subs if ev == 'stock_updated']) == 4)

    # died, then every replacement fails to start (unstarted Thread): replacement retries stay on
    # that one new object, bounded; no further constructions
    log, out, err, S, P = _run_loop("1", cycles=14, purchase_fails="unstarted",
                                    purchase_short_lived=True, purchase_fail_after=1)
    check("v2 purchase died, replacement start raises: exactly ONE replacement built, 3 retries, abandoned",
          err is None and len(P.made) == 2 and P.starts == 1 + 1 + 3
          and out.count("purchase_thread start abandoned after 3 attempts") == 1,
          f"{err!r} {len(P.made)}/{P.starts}\n{out[-700:]}")
    check("v2 purchase died, replacement start raises: subscribers = original + 1 replacement",
          len([1 for (ev, cb) in P.bus.subs if ev == 'stock_updated']) == 2)

    # ---- v2: never a replacement while an earlier object of that kind is still alive ------------
    alive_old = SimpleNamespace(thread=threading.Thread(target=time.sleep, args=(5,), daemon=True))
    alive_old.thread.start()
    dead_cur = SimpleNamespace(thread=threading.Thread(target=lambda: None), _tsg_alive_once=True)
    dead_cur.thread.start()
    dead_cur.thread.join()
    built = []
    g3 = {"os": os, "time": time, "threading": threading, "event_bus": None, "shared_data": None,
          "global_purchase_manager": None, "global_event_loop": None,
          "add_activity_log": lambda *a, **k: None,
          "StockMonitorThread": lambda *a, **k: built.append("s"),
          "PurchaseManagerThread": lambda *a, **k: built.append("p")}
    exec(_load_funcs(), g3)
    f3 = {"objs_purchase": [alive_old]}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _ in range(3):
            r = g3["_tsg_health_one"]("purchase", dead_cur, f3, lambda: built.append("p") or SimpleNamespace())
    check("v2 died while an earlier object is alive: NO replacement constructed",
          built == [] and r is dead_cur and f3.get("repl_purchase", 0) == 0
          and buf.getvalue().count("earlier purchase_thread object is still alive") == 1, buf.getvalue())

    # ---- v2: healthy boot over many cycles: markers once each, nothing else ---------------------
    log, out, err, S, P = _run_loop("1", cycles=8)
    check("v2 healthy x8 cycles: [OK] markers exactly once each, no [ERROR], no new objects",
          out.count("[OK] purchase_thread started") == 1 and out.count("[OK] stock_thread started") == 1
          and "[ERROR]" not in out and len(S.made) == 1 and len(P.made) == 1)
    check("v2 healthy: alive_once recorded on both wrappers",
          getattr(S.made[0], "_tsg_alive_once", False) and getattr(P.made[0], "_tsg_alive_once", False))

    # ---- v2: KeyboardInterrupt / SystemExit from start() still propagate --------------------------
    for exc in (KeyboardInterrupt, SystemExit):
        class _KI:
            thread = None

            def start(self):
                raise exc()
        g4 = {"os": os, "time": time, "threading": threading}
        exec(_load_funcs(), g4)
        got = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                g4["_start_buy_threads_guarded"](_KI(), _KI())
        except BaseException as e:
            got = e
        check(f"v2 {exc.__name__} from the initial start() propagates", isinstance(got, exc), repr(got))
        got = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                g4["_tsg_start_attempt"]("x", _KI(), {})
        except BaseException as e:
            got = e
        check(f"v2 {exc.__name__} from a retry start() propagates", isinstance(got, exc), repr(got))

    # ---- ON, both raise: still no crash ------------------------------------------
    log, out, err, S, P = _run_loop("1", stock_fails=True, purchase_fails=True)
    check("ON + both raise: loop did not raise", err is None, repr(err))
    check("ON + both raise: error lines for both", out.count("start failed") >= 2, out[-300:])

    # ---- helper in isolation ------------------------------------------------------
    log2 = []

    class T:
        def __init__(self, n, boom):
            self.n, self.boom, self.thread = n, boom, None

        def start(self):
            log2.append(self.n)
            if self.boom:
                raise RuntimeError("x")

    g2 = {"os": os, "time": time, "threading": threading}
    exec(_load_funcs(), g2)
    with contextlib.redirect_stdout(io.StringIO()):
        r = g2["_start_buy_threads_guarded"](T("s", True), T("p", False))
    check("helper: purchase first, returns per-thread result",
          log2 == ["p", "s"] and r == {"purchase": True, "stock": False}, f"{log2} {r}")

    # ---- static: default path statements preserved ---------------------------------
    for stmt in ("stock_thread.start()", "purchase_thread.start()"):
        check(f"static: unguarded `{stmt}` still present in else-branch", ("        " + stmt + "\n") in APP)
    check("static: flag defaults to '0'", "os.environ.get('TARGET_THREAD_START_GUARD', '0')" in APP)

    print()
    if _fails:
        print(f"===== {len(_fails)} FAILED: {_fails}")
        return 1
    print("===== ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

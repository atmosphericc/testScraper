#!/usr/bin/env python3
"""Offline tests for F1 (2026-10-18) TARGET_THREAD_START_GUARD.

10-07: an AttributeError in StockMonitorThread.start() stopped monitoring_loop before
purchase_thread.start(): process alive, unable to buy. With TARGET_THREAD_START_GUARD=1
the purchase thread is started FIRST and each start() has its own try/except; default
(flag unset/0) keeps the old sequential unguarded code path (stock first, no guard).

The REAL monitoring_loop / helper source is lifted out of app.py with ast and executed
against stub threads (importing app.py would pull in the whole bot). No browser, no network.
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
         "_health_check_guarded", "monitoring_loop")

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


class _Base:
    """Stub of StockMonitorThread / PurchaseManagerThread."""
    log = None
    fail_start = False
    made = None

    def __init__(self, *a, **kw):
        self.thread = None
        self.stock_monitor = object()
        self.stopped = False
        type(self).made.append(self)

    def start(self):
        self.log.append(self.NAME)
        if type(self).fail_start:
            raise AttributeError(f"{self.NAME} diag boom")
        self.thread = threading.Thread(target=time.sleep, args=(30,), daemon=True)
        self.thread.start()

    def set_stock_monitor(self, sm):
        pass

    def stop(self):
        self.stopped = True


def _run_loop(flag, stock_fails=False, purchase_fails=False):
    """Execute the real monitoring_loop with stubs."""
    log = []

    class Stock(_Base):
        NAME = "stock"

    class Purchase(_Base):
        NAME = "purchase"

    for cls, fails in ((Stock, stock_fails), (Purchase, purchase_fails)):
        cls.log = log
        cls.fail_start = fails
        cls.made = []

    shared = SimpleNamespace(lock=_Lock(), monitor_running=False)
    sleeps = {"n": 0}

    def fake_sleep(s):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:  # let two health cycles run, then stop the loop
            shared.monitor_running = False

    ns = {
        "os": os, "time": SimpleNamespace(sleep=fake_sleep, time=time.time), "threading": threading,
        "StockMonitorThread": Stock, "PurchaseManagerThread": Purchase,
        "event_bus": object(), "shared_data": shared,
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
    check("OFF healthy: no new-code markers", "start failed" not in out)

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
    check("ON + stock raises: health check retried stock, capped (original + <=3)",
          1 < len(S.made) <= 4, str(len(S.made)))

    # ---- ON, purchase start raises: stock still started, error line --------------
    log, out, err, S, P = _run_loop("1", purchase_fails=True)
    check("ON + purchase raises: loop did not raise", err is None, repr(err))
    check("ON + purchase raises: stock still started", "stock" in log)
    check("ON + purchase raises: error line names purchase_thread",
          "[MONITORING_LOOP] [ERROR] purchase_thread start failed: "
          "AttributeError('purchase diag boom')" in out)

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

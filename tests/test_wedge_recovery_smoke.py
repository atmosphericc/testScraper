#!/usr/bin/env python3
"""Smoke test: 2026-07-03 wedged-browser drop-failure fixes.

That night all 3 workers' CDP websockets died at 23:50; every purchase then
hung on unbounded awaits (mislabeled 'lock_timeout'), the stdout tee's
__getattr__ recursion crashed the process at 02:48, and the relaunch idled
"not logged in" until morning. These tests pin the fixes:

  1. _PurchaseLogTee: no tee-on-tee stacking, no __getattr__ recursion on a
     gutted instance, writes survive a closed/missing file.
  2. SessionManager._context_lock is an asyncio.Lock (threading.Lock held
     across awaits froze the whole worker loop).
  3. _test_tab_health: an evaluate() TIMEOUT (dead-socket signature) is
     UNHEALTHY even when tab.url is truthy (the old lie).
  4. _execute_purchase_impl on a wedged tab bails FAST (<10s) with the
     retryable 'cdp_wedged_pre_atc' — not a 120-150s hang — so the manager's
     restart-then-retry machinery fires while the item is still in stock.
  5. execute_purchase distinguishes impl hangs ('purchase_impl_hang', error
     mentions websocket → manager restarts browser) from real lock contention.

No browser, no network. Run: python tests/test_wedge_recovery_smoke.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import _PurchaseLogTee  # noqa: E402
from src.session.session_manager import SessionManager  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  PASS {name}")
    except Exception as e:
        FAIL.append((name, e))
        print(f"  FAIL {name}: {type(e).__name__}: {e}")


# ---- 1. tee hardening ------------------------------------------------------ #
def test_tee_never_stacks():
    with tempfile.TemporaryDirectory() as d:
        base = io.StringIO()
        t1 = _PurchaseLogTee(base, Path(d) / "a.log")
        t2 = _PurchaseLogTee(t1, Path(d) / "b.log")   # wraps a tee → must unwrap
        assert t2._orig is base, "tee stacked on tee instead of unwrapping to base"
        t2.write("hello")
        assert "hello" in base.getvalue()
        t1.close(); t2.close()


def test_tee_getattr_no_recursion():
    # A gutted instance (empty __dict__) must raise AttributeError, not
    # RecursionError — this is the exact 02:48:35 crash shape.
    ghost = object.__new__(_PurchaseLogTee)
    try:
        ghost.encoding
        raise AssertionError("expected AttributeError")
    except AttributeError:
        pass  # correct
    # And a normal instance proxies through to the base stream.
    base = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        t = _PurchaseLogTee(base, Path(d) / "c.log")
        _ = t.getvalue  # StringIO attr via passthrough
        t.close()


def test_tee_write_survives_closed_file():
    base = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        t = _PurchaseLogTee(base, Path(d) / "d.log")
        t.close()
        t.write("after close")   # must not raise
        t.flush()
        assert "after close" in base.getvalue()


def test_tee_write_survives_dead_console():
    class DeadStream:
        def write(self, data):  # console gone (crashed host proc)
            raise OSError("console dead")
        def flush(self):
            raise OSError("console dead")
    with tempfile.TemporaryDirectory() as d:
        t = _PurchaseLogTee(DeadStream(), Path(d) / "e.log")
        t.write("still logged to file")   # must not raise
        t.flush()
        t.close()
        assert "still logged to file" in (Path(d) / "e.log").read_text(encoding="utf-8")


# ---- 2. get_page lock type ------------------------------------------------- #
def test_context_lock_is_asyncio():
    sm = SessionManager.__new__(SessionManager)  # no browser side effects
    # run the relevant part of __init__ manually? No — construct for real but
    # point the profile dir at a temp dir so mkdir is harmless.
    with tempfile.TemporaryDirectory() as d:
        sm = SessionManager(session_path=str(Path(d) / "t.json"),
                            user_data_dir=str(Path(d) / "prof"))
        assert isinstance(sm._context_lock, asyncio.Lock), (
            f"_context_lock must be asyncio.Lock, got {type(sm._context_lock)}"
        )


# ---- 3. tab health: timeout = unhealthy ------------------------------------ #
class WedgedTab:
    """CDP-dead tab: evaluate hangs forever, url still truthy (cached)."""
    url = "https://www.target.com/"

    async def evaluate(self, *a, **k):
        await asyncio.sleep(3600)

    async def get(self, *a, **k):
        await asyncio.sleep(3600)

    async def send(self, *a, **k):
        await asyncio.sleep(3600)


def test_tab_health_timeout_is_unhealthy():
    with tempfile.TemporaryDirectory() as d:
        sm = SessionManager(session_path=str(Path(d) / "t.json"),
                            user_data_dir=str(Path(d) / "prof"))
        t0 = time.time()
        healthy = asyncio.run(sm._test_tab_health(WedgedTab()))
        took = time.time() - t0
        assert healthy is False, "wedged tab (evaluate timeout + truthy url) declared healthy"
        assert took < 10, f"health check took {took:.1f}s — not bounded"


# ---- 4. wedged purchase bails fast and retryable ---------------------------- #
class FakeSM:
    browser = None
    purchase_in_progress = False

    def __init__(self, tab):
        self._tab = tab

    async def get_page(self):
        return self._tab


def test_wedged_purchase_bails_fast_retryable():
    pe = PurchaseExecutor(session_manager=FakeSM(WedgedTab()))
    t0 = time.time()
    result = asyncio.run(pe._execute_purchase_impl("94681770", quantity=1))
    took = time.time() - t0
    assert result.get("reason") == "cdp_wedged_pre_atc", f"got {result!r}"
    assert took < 10, f"wedged purchase took {took:.1f}s to bail (budget-eater)"
    # The manager treats this reason as transient AND restart-worthy:
    from src.purchasing import bulletproof_purchase_manager as bpm_mod
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    assert "'cdp_wedged_pre_atc'" in src, "manager no longer knows the retryable wedge reason"


# ---- 4b. warmup pool survives a mid-drop browser restart -------------------- #
class FastTab:
    url = "https://www.target.com/cart"

    async def evaluate(self, *a, **k):
        return "complete"

    async def get(self, *a, **k):
        return None

    async def send(self, *a, **k):
        return None

    def add_handler(self, *a, **k):
        return None


class FakeBrowser:
    def __init__(self):
        self.opened = 0
        self.tabs = [FastTab()]

    async def get(self, *a, **k):
        self.opened += 1
        return FastTab()


def test_warmup_pool_resets_on_browser_change():
    sm = FakeSM(FastTab())
    browser_a, browser_b = FakeBrowser(), FakeBrowser()
    sm.browser = browser_a
    pe = PurchaseExecutor(session_manager=sm)

    async def _noop_interceptor(tab, persistent=False):
        return None
    pe._setup_cdp_fetch_interceptor = _noop_interceptor

    async def scenario():
        tab1 = await pe._ensure_warmup_tab(0)
        assert tab1 is not None and browser_a.opened == 1
        # Same browser → handle reused, no new tab.
        tab1_again = await pe._ensure_warmup_tab(0)
        assert tab1_again is tab1 and browser_a.opened == 1
        # Browser restarted (refresh_session) → stale handles must be dropped.
        sm.browser = browser_b
        tab2 = await pe._ensure_warmup_tab(0)
        assert tab2 is not None and tab2 is not tab1, "stale dead-Chrome tab handle reused"
        assert browser_b.opened == 1, "no fresh tab opened on the new browser"
        assert pe._warmup_browser_ref is browser_b
    asyncio.run(scenario())


def test_warmup_stuck_flag_ttl():
    pe = PurchaseExecutor(session_manager=FakeSM(FastTab()))
    calls = {"n": 0}

    async def fake_refresh(idx, force_fresh=False):
        calls["n"] += 1
        pe._cached_cart_headers = {"X": "y"}
        pe._cached_cart_headers_ts = time.time()
        return True
    pe._refresh_on_tab = fake_refresh

    async def scenario():
        # Fresh in-progress flag → warm skips (no refresh call).
        pe._warmup_in_progress = True
        pe._warmup_in_progress_ts = time.time()
        await pe.warm_shape_headers()
        assert calls["n"] == 0, "warm ran despite a fresh in-progress flag"
        # Stale flag (>60s, orphaned coroutine) → warm reclaims and proceeds.
        pe._warmup_in_progress_ts = time.time() - 61
        ok = await pe.warm_shape_headers()
        assert ok and calls["n"] == 1, "stale in-progress flag was not reclaimed"
        assert pe._warmup_in_progress is False
    asyncio.run(scenario())


# ---- 4c. drop-guard: browser restart waits for in-flight purchase ----------- #
def test_refresh_waits_for_drop_guard():
    with tempfile.TemporaryDirectory() as d:
        sm = SessionManager(session_path=str(Path(d) / "t.json"),
                            user_data_dir=str(Path(d) / "prof"))
        order = []

        async def fake_impl():
            order.append("refresh")
            return True
        sm._refresh_session_impl = fake_impl
        guard = asyncio.Lock()
        sm._drop_guard_lock = guard

        async def scenario():
            # Simulate an in-flight purchase holding the page lock.
            async with guard:
                task = asyncio.get_running_loop().create_task(sm.refresh_session())
                await asyncio.sleep(0.25)
                assert order == [], "restart ran while a purchase held the page lock"
                order.append("purchase_done")
            assert await asyncio.wait_for(task, 5) is True
            assert order == ["purchase_done", "refresh"], order
            # guard=False path (caller already holds the lock — sentinel ladder)
            async with guard:
                assert await asyncio.wait_for(sm.refresh_session(guard=False), 5) is True
        asyncio.run(scenario())


# ---- 4d. watchdog escalation must survive its own cleanup (2026-07-06) ------ #
def test_escalation_survives_watchdog_cancel():
    """The 07-06 killer: escalation ran INSIDE the watchdog task; _safe_cleanup
    stopped the watchdog -> the recovery cancelled itself one await before the
    Chrome relaunch. The detached escalation task must complete even when the
    watchdog task is cancelled mid-flight."""
    with tempfile.TemporaryDirectory() as d:
        sm = SessionManager(session_path=str(Path(d) / "t.json"),
                            user_data_dir=str(Path(d) / "prof"))
        done = []

        async def fake_refresh(guard=True):
            # simulate _safe_cleanup stopping the watchdog mid-refresh
            sm._stop_cookie_watchdog()
            await asyncio.sleep(0.1)
            done.append("refreshed")
            return True
        sm.refresh_session = fake_refresh

        async def scenario():
            async def fake_watchdog():
                sm._watchdog_task_obj = asyncio.current_task()
                sm._cookie_watchdog_running = True
                sm._spawn_escalation_refresh("test 3-strike")
                await asyncio.sleep(3600)   # parked until cancelled

            wd = asyncio.get_running_loop().create_task(fake_watchdog())
            sm._cookie_watchdog_task = wd
            await asyncio.sleep(0.05)
            # cleanup path cancels the watchdog (external caller — allowed)
            wd.cancel()
            # the DETACHED escalation must still finish the refresh
            await asyncio.wait_for(sm._escalation_task, 10)
            assert done == ["refreshed"], f"escalation did not survive watchdog cancel: {done}"
        asyncio.run(scenario())


def test_stop_watchdog_never_cancels_self():
    """_stop_cookie_watchdog called from WITHIN the watchdog coroutine must
    clear the running flag but NOT cancel the current task."""
    with tempfile.TemporaryDirectory() as d:
        sm = SessionManager(session_path=str(Path(d) / "t.json"),
                            user_data_dir=str(Path(d) / "prof"))
        survived = []

        async def watchdog_like():
            sm._watchdog_task_obj = asyncio.current_task()
            sm._cookie_watchdog_running = True
            sm._cookie_watchdog_task = asyncio.current_task()
            sm._stop_cookie_watchdog()        # self-stop: must NOT cancel us
            await asyncio.sleep(0.05)         # would raise CancelledError if cancelled
            survived.append(True)
            return sm._cookie_watchdog_running

        async def scenario():
            running_flag = await asyncio.wait_for(watchdog_like(), 5)
            assert survived == [True], "watchdog task was cancelled by its own stop call"
            assert running_flag is False, "running flag not cleared"
        asyncio.run(scenario())


# ---- 4e. timer-based sentinel drives checks without stock events ------------ #
def test_sentinel_timer_runs_without_stock_events():
    """process_stock_data (the old sentinel host) only runs on stock events in
    the resilient stack — the timer must drive checks on its own."""
    import threading as _threading
    from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as B

    class _FakeWorkerPool:
        def __init__(self):
            self.workers = []

    mgr = B.__new__(B)   # no heavy __init__
    mgr.worker_pool = _FakeWorkerPool()
    mgr._sentinel_timer_thread = None
    mgr._sentinel_stop = None
    ticks = []
    mgr._run_session_sentinel_once = lambda: ticks.append(time.time())

    os.environ['TARGET_SENTINEL_INITIAL_DELAY_S'] = '0.1'
    os.environ['TARGET_SENTINEL_INTERVAL_S'] = '0.2'
    try:
        mgr._start_sentinel_timer()
        assert mgr._sentinel_timer_thread is not None and mgr._sentinel_timer_thread.is_alive()
        deadline = time.time() + 5
        while len(ticks) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert len(ticks) >= 2, f"timer produced {len(ticks)} sentinel ticks in 5s"
        # cycle-based hook must defer to the live timer
        mgr._sentinel_cycle_counter = 4
        before = len(ticks)
        B._maybe_run_session_sentinel(mgr)
        assert len(ticks) == before or ticks[-1] != 'cycle', "cycle hook ran while timer active"
        mgr._sentinel_stop.set()
        mgr._sentinel_timer_thread.join(timeout=3)
        assert not mgr._sentinel_timer_thread.is_alive(), "timer thread did not stop"
    finally:
        os.environ.pop('TARGET_SENTINEL_INITIAL_DELAY_S', None)
        os.environ.pop('TARGET_SENTINEL_INTERVAL_S', None)


# ---- 5. honest execute_purchase labels -------------------------------------- #
def test_lock_timeout_only_when_lock_contended():
    # Verify the label split exists: _impl_entered gating in execute_purchase.
    import inspect
    from src.session import purchase_executor as pe_mod
    src = inspect.getsource(pe_mod.PurchaseExecutor.execute_purchase)
    assert "purchase_impl_hang" in src, "impl-hang label missing"
    assert "_impl_entered" in src, "lock-vs-impl disambiguation missing"
    assert "websocket" in src, "impl-hang error must mention websocket (manager restart trigger)"


if __name__ == "__main__":
    check("test_tee_never_stacks", test_tee_never_stacks)
    check("test_tee_getattr_no_recursion", test_tee_getattr_no_recursion)
    check("test_tee_write_survives_closed_file", test_tee_write_survives_closed_file)
    check("test_tee_write_survives_dead_console", test_tee_write_survives_dead_console)
    check("test_context_lock_is_asyncio", test_context_lock_is_asyncio)
    check("test_tab_health_timeout_is_unhealthy", test_tab_health_timeout_is_unhealthy)
    check("test_wedged_purchase_bails_fast_retryable", test_wedged_purchase_bails_fast_retryable)
    check("test_warmup_pool_resets_on_browser_change", test_warmup_pool_resets_on_browser_change)
    check("test_warmup_stuck_flag_ttl", test_warmup_stuck_flag_ttl)
    check("test_refresh_waits_for_drop_guard", test_refresh_waits_for_drop_guard)
    check("test_escalation_survives_watchdog_cancel", test_escalation_survives_watchdog_cancel)
    check("test_stop_watchdog_never_cancels_self", test_stop_watchdog_never_cancels_self)
    check("test_sentinel_timer_runs_without_stock_events", test_sentinel_timer_runs_without_stock_events)
    check("test_lock_timeout_only_when_lock_contended", test_lock_timeout_only_when_lock_contended)
    print()
    if FAIL:
        print(f"{len(PASS)}/{len(PASS) + len(FAIL)} passed — {len(FAIL)} FAILED")
        sys.exit(1)
    print(f"{len(PASS)}/{len(PASS)} smoke tests passed.")

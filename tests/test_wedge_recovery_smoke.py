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
    check("test_lock_timeout_only_when_lock_contended", test_lock_timeout_only_when_lock_contended)
    print()
    if FAIL:
        print(f"{len(PASS)}/{len(PASS) + len(FAIL)} passed — {len(FAIL)} FAILED")
        sys.exit(1)
    print(f"{len(PASS)}/{len(PASS)} smoke tests passed.")

#!/usr/bin/env python3
"""Offline test: TARGET_SENTINEL_DEFER (2026-11-04). No browser, no network.

Run: python tests/test_sentinel_defer_1104.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing import bulletproof_purchase_manager as M  # noqa: E402
B = M.BulletproofPurchaseManager

_KNOBS = ("TARGET_SENTINEL_DEFER", "TARGET_SENTINEL_DEFER_RECENT_S", "TARGET_SENTINEL_QUIET_LOCAL",
          "TARGET_SENTINEL_DEFER_MIN_TOKEN_S", "TARGET_STOCK_PROBE")


def _clean():
    for k in _KNOBS:
        os.environ.pop(k, None)


def _st(now, last_true=0.0, window_start=0.0, last_checked=None):
    return SimpleNamespace(
        last_checked_at=now if last_checked is None else last_checked,
        last_true_at=last_true, window_start_at=window_start, last_false_at=now - 1)


def _mgr(statuses):
    m = B.__new__(B)
    m._sentinel_lock = threading.Lock()
    m._account_health = {}
    m.stock_monitor = SimpleNamespace(_resilient_checker=SimpleNamespace(_tcin_status=statuses))
    return m


def test_flag_off_never_defers():
    _clean()
    now = time.time()
    m = _mgr({"1": _st(now, last_true=now, window_start=now - 1)})
    assert m._sentinel_defer_reason("W1/a1", token_ttl_s=99999, now=now) is None
    os.environ["TARGET_SENTINEL_DEFER"] = "yes"   # junk = off
    assert m._sentinel_defer_reason("W1/a1", token_ttl_s=99999, now=now) is None
    os.environ["TARGET_SENTINEL_DEFER"] = "0"
    assert m._sentinel_defer_reason("W1/a1", now=now) is None
    _clean()


def test_live_defers():
    _clean(); os.environ["TARGET_SENTINEL_DEFER"] = "1"
    now = time.time()
    m = _mgr({"1": _st(now, last_true=now - 2, window_start=now - 500)})
    r = m._sentinel_defer_reason("W1/a1", token_ttl_s=5000, now=now)
    assert r and r.startswith("live") and "ttl_s=5000" in r, r
    # uniform token rule: unknown / short / expired TTL -> run the check
    for bad in (None, 600, 0, -5):
        assert m._sentinel_defer_reason("W1/a1", token_ttl_s=bad, now=now) is None, bad
    # probe off -> cannot tell -> run the check
    os.environ["TARGET_STOCK_PROBE"] = "0"
    assert m._sentinel_defer_reason("W1/a1", token_ttl_s=5000, now=now) is None
    _clean()


def test_recent_flip_window():
    _clean(); os.environ["TARGET_SENTINEL_DEFER"] = "1"
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = ""
    now = time.time()
    # window opened 100 s ago, last true 60 s ago (not live) -> recent_flip
    m = _mgr({"1": _st(now, last_true=now - 60, window_start=now - 100)})
    r = m._sentinel_defer_reason("a", token_ttl_s=5000, now=now)
    assert r and r.startswith("recent_flip"), r
    assert m._sentinel_defer_reason("a", token_ttl_s=None, now=now) is None
    assert m._sentinel_defer_reason("a", token_ttl_s=100, now=now) is None
    # opened 400 s ago -> outside default 180 s
    m = _mgr({"1": _st(now, last_true=now - 390, window_start=now - 400)})
    assert m._sentinel_defer_reason("a", token_ttl_s=5000, now=now) is None
    # custom window
    os.environ["TARGET_SENTINEL_DEFER_RECENT_S"] = "500"
    assert (m._sentinel_defer_reason("a", token_ttl_s=5000, now=now) or "").startswith("recent_flip")
    # never in stock
    m = _mgr({"1": _st(now)})
    assert m._sentinel_defer_reason("a", token_ttl_s=5000, now=now) is None
    _clean()


def test_quiet_window_helper_wrap():
    _clean()
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = "22:00-02:00"
    d = lambda h, mi: datetime(2026, 11, 4, h, mi)
    assert M._sentinel_quiet_active(d(23, 0)) and M._sentinel_quiet_active(d(1, 59))
    assert M._sentinel_quiet_active(d(22, 0)) and not M._sentinel_quiet_active(d(2, 0))
    assert not M._sentinel_quiet_active(d(12, 0))
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = "01:30-04:30"
    assert M._sentinel_quiet_active(d(3, 0)) and not M._sentinel_quiet_active(d(5, 0))
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = ""
    assert not M._sentinel_quiet_active(d(3, 0))
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = "junk"
    assert not M._sentinel_quiet_active(d(3, 0))
    _clean()


def test_quiet_window_token_margin():
    _clean(); os.environ["TARGET_SENTINEL_DEFER"] = "1"
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = "00:00-23:59"
    now = time.time()
    m = _mgr({"1": _st(now)})
    r = m._sentinel_defer_reason("a", token_ttl_s=5000, now=now)
    assert r and r.startswith("quiet_window"), r
    assert m._sentinel_defer_reason("a", token_ttl_s=600, now=now) is None   # inside margin
    assert m._sentinel_defer_reason("a", token_ttl_s=None, now=now) is None  # unknown expiry
    os.environ["TARGET_SENTINEL_DEFER_MIN_TOKEN_S"] = "300"
    assert m._sentinel_defer_reason("a", token_ttl_s=600, now=now)
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = ""
    assert m._sentinel_defer_reason("a", token_ttl_s=5000, now=now) is None
    _clean()


class _SM:
    def __init__(self, ttl=-1.0):
        self.token_reads = 0
        self.purchase_in_progress = False
        self.nav_calls = 0
        self._ttl = ttl

    async def ensure_logged_in(self):
        self.nav_calls += 1
        return True

    async def get_access_token_status(self, tab=None):
        self.token_reads += 1
        return {'present': self._ttl > 0, 'member': self._ttl > 0, 'ttl_s': self._ttl}


class _W:
    def __init__(self, sm):
        self.session_manager = sm
        self.purchase_executor = None

    def label(self):
        return "W1/a1"

    def run_async(self, coro):
        import asyncio
        f = Future()
        f.set_result(asyncio.new_event_loop().run_until_complete(coro))
        return f


def test_deferred_branch_does_not_navigate():
    _clean(); os.environ["TARGET_SENTINEL_DEFER"] = "1"
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = ""
    now = time.time()
    live = {"1": _st(now, last_true=now - 1, window_start=now - 5)}
    m = _mgr(live)
    sm = _SM(ttl=5000.0)
    m._run_session_sentinel_for([_W(sm)])
    assert sm.nav_calls == 0, "deferred check must not navigate"
    assert sm.token_reads == 1
    assert m.get_account_health() == {}, "deferred check must not record health"
    # live but token short / unknown (uniform token rule) -> the check runs
    sm2 = _SM(ttl=300.0)
    m._run_session_sentinel_for([_W(sm2)])
    assert sm2.nav_calls == 1 and sm2.token_reads == 1
    sm3 = _SM(ttl=-1.0)
    m._run_session_sentinel_for([_W(sm3)])
    assert sm3.nav_calls == 1
    # flag off -> same state navigates, token never read (prior behaviour)
    os.environ["TARGET_SENTINEL_DEFER"] = "0"
    sm4 = _SM(ttl=5000.0)
    m._run_session_sentinel_for([_W(sm4)])
    assert sm4.nav_calls == 1 and sm4.token_reads == 0
    assert m.get_account_health()["W1/a1"]["logged_in"] is True
    # flag on, no reason applies -> runs and the token read is NOT called
    os.environ["TARGET_SENTINEL_DEFER"] = "1"
    m2 = _mgr({"1": _st(now)})
    sm5 = _SM(ttl=5000.0)
    m2._run_session_sentinel_for([_W(sm5)])
    assert sm5.nav_calls == 1 and sm5.token_reads == 0, "no reason -> no browser read"
    # quiet window on: healthy token -> deferred; short / unknown -> runs
    os.environ["TARGET_SENTINEL_QUIET_LOCAL"] = "00:00-23:59"
    for ttl, nav in ((50000.0, 0), (300.0, 1), (-1.0, 1)):
        s_ = _SM(ttl=ttl)
        m2._run_session_sentinel_for([_W(s_)])
        assert (s_.nav_calls, s_.token_reads) == (nav, 1), (ttl, s_.nav_calls, s_.token_reads)
    _clean()


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t(); print(f"  PASS {t.__name__}"); passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL {t.__name__}: {e}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} tests passed.")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())

#!/usr/bin/env python3
"""Smoke test: post-denial cart hold check (2026-08-14).

Community-documented on the 30th Anniversary preorder drops: Target sometimes
holds the item in the cart while the add response reports the high-demand
denial (429 DCO_RATE_LIMITED); retail users recover by reloading /cart until
the item appears. PurchaseExecutor._check_cart_hold is the API version of that
reload: after a gate-denied ATC, one Endpoint 6 GET; if our TCIN is already in
cart_items, the executor rides the cart_confirmed path to checkout instead of
bailing.

Pins:
  - True ONLY on a provable hit (ok + our tcin in cart_items)
  - kill-switch (TARGET_CART_HOLD_CHECK=0 -> _cart_hold_check_on False) short-
    circuits without touching the tab
  - per-executor rate limit: a second call inside the interval never fires
  - miss / non-dict / not-ok / evaluate error all return False, never raise
  - source contract: the 429 fast-bail and the post-fast-retry fall-through
    both consult _check_cart_hold; __init__ wires the env knobs; the cart read
    is wrapped in wait_for (bounded)

No browser, no network. Run: python tests/test_cart_hold_check.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

SRC = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


class _FakeTab:
    def __init__(self, result=None, exc=None):
        self.calls = 0
        self._result = result
        self._exc = exc

    async def evaluate(self, _js, await_promise=False):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._result


def _executor(on=True, interval=4.0):
    ex = object.__new__(PurchaseExecutor)
    ex._cart_hold_check_on = on
    ex._cart_hold_check_interval_s = interval
    ex._last_cart_hold_check_ts = 0.0
    return ex


def _run(ex, tab, tcin="123", status=429):
    return asyncio.run(ex._check_cart_hold(tab, tcin, status))


def test_hit_returns_true():
    tab = _FakeTab(result={"ok": True, "tcins": ["999", "123"]})
    check("hit: our tcin in cart -> True", _run(_executor(), tab) is True)
    check("hit: tab was actually queried", tab.calls == 1)


def test_miss_returns_false():
    tab = _FakeTab(result={"ok": True, "tcins": ["999"]})
    check("miss: other tcins only -> False", _run(_executor(), tab) is False)


def test_empty_cart_returns_false():
    tab = _FakeTab(result={"ok": True, "tcins": []})
    check("miss: empty cart -> False", _run(_executor(), tab) is False)


def test_not_ok_returns_false():
    tab = _FakeTab(result={"ok": False, "status": 401})
    check("cart GET not ok -> False", _run(_executor(), tab) is False)


def test_non_dict_returns_false():
    tab = _FakeTab(result="garbage")
    check("non-dict evaluate result -> False", _run(_executor(), tab) is False)


def test_evaluate_error_never_raises():
    tab = _FakeTab(exc=RuntimeError("cdp dead"))
    check("evaluate raising -> False, no raise", _run(_executor(), tab) is False)


def test_kill_switch_short_circuits():
    tab = _FakeTab(result={"ok": True, "tcins": ["123"]})
    ex = _executor(on=False)
    check("kill-switch -> False", _run(ex, tab) is False)
    check("kill-switch: tab never touched", tab.calls == 0)


def test_rate_limit_inside_interval():
    tab = _FakeTab(result={"ok": True, "tcins": ["123"]})
    ex = _executor(interval=60.0)
    first = _run(ex, tab)
    second = _run(ex, tab)
    check("first call inside interval fires", first is True and tab.calls == 1)
    check("second call inside interval suppressed", second is False and tab.calls == 1)


def test_zero_interval_always_fires():
    tab = _FakeTab(result={"ok": True, "tcins": ["123"]})
    ex = _executor(interval=0.0)
    _run(ex, tab)
    _run(ex, tab)
    check("interval=0 -> every call fires", tab.calls == 2)


def test_source_contract():
    check("__init__ wires TARGET_CART_HOLD_CHECK", "TARGET_CART_HOLD_CHECK'" in SRC
          and "_cart_hold_check_on" in SRC)
    check("__init__ wires TARGET_CART_HOLD_CHECK_INTERVAL_S",
          "TARGET_CART_HOLD_CHECK_INTERVAL_S" in SRC)
    # 429 fast-bail consults the hold check before returning rate_limited_429
    _i429 = SRC.find("neither recovers a 429")
    _bail = SRC.find("'reason': 'rate_limited_429'", _i429)
    _hold = SRC.find("_check_cart_hold", _i429)
    check("429 fast-bail consults _check_cart_hold first",
          _i429 != -1 and _hold != -1 and _bail != -1 and _hold < _bail)
    # post-fast-retry fall-through consults it too (after token_fresh = False)
    _itf = SRC.find("token_fresh = False")
    check("post-fast-retry fall-through consults _check_cart_hold",
          _itf != -1 and "_check_cart_hold" in SRC[_itf:_itf + 600])
    # the cart read is bounded
    _ihold = SRC.find("async def _check_cart_hold")
    check("cart read wrapped in wait_for (bounded)",
          _ihold != -1 and "wait_for" in SRC[_ihold:_ihold + 2000])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

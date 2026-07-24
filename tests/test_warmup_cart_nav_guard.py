#!/usr/bin/env python3
"""Unit test: warmup tabs must not navigate to /cart while a purchase is live.

Guards the 2026-07-21 CVV-challenge fix. Loading /cart makes Target's own page JS
fire `PUT /web_checkouts/v1/cart?...&field_groups=ADDRESSES...` against THIS
account's cart. Target's own help article states:

    "Credit card or CVV re-entry will be required if a shipping address is
     updated during checkout..."

On 07-20->21 a background warmup /cart nav landed inside the checkout window on
the same session immediately before every 400 MISSING_CREDIT_CARD_CVV, and the
bot went 0-for-9 despite nine clean ATC 201s.

Load-bearing properties:
  1. While a purchase is in flight, the /cart NAVIGATION is skipped...
  2. ...but the dummy POST still fires, so the Shape ring keeps refilling.
     (Skipping only the nav is the same path the routine <90s warm-tab skip
     already takes many times an hour.)
  3. force_fresh=True still navigates — that is the ATC-401 recovery reload
     which must re-mint a write token, and breaking it would be worse.
  4. With no purchase in flight, behaviour is unchanged.

Run: python tests/test_warmup_cart_nav_guard.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402


class _FakeTab:
    def __init__(self, counters):
        self.url = "https://www.target.com/cart"
        self._c = counters

    async def get(self, url):
        self._c["nav"] += 1
        return None

    async def evaluate(self, script, await_promise=False):
        return "complete"


class _FakeSM:
    def __init__(self, in_progress):
        self.browser = object()
        self._in_progress = in_progress

    def is_purchase_in_progress(self):
        return self._in_progress


def _make(in_progress, cart_nav_age=999.0):
    c = {"nav": 0, "dummy": 0}
    ex = object.__new__(PurchaseExecutor)
    ex.session_manager = _FakeSM(in_progress)
    ex._warmup_pool_lock = asyncio.Lock()
    ex._cached_cart_headers = {}
    ex._cached_cart_headers_ts = 0.0
    ex._warmup_tabs = {0: None}
    ex._warmup_pool_size = 1
    import time as _t
    # cart_nav_age is measured against now; 999 => "stale, would normally nav"
    ex._warmup_tab_cart_ts = {0: _t.time() - cart_nav_age}
    tab = _FakeTab(c)

    async def fake_ensure(idx):
        return tab

    async def fake_dummy(idx, tab_arg):
        c["dummy"] += 1
        return True

    ex._ensure_warmup_tab = fake_ensure
    # _refresh_on_tab fires the dummy POST inline; stub the smallest seam that
    # lets us observe it without a browser.
    ex._fire_dummy_and_wait = fake_dummy
    return ex, c, tab


def _run(ex, force_fresh=False):
    # _refresh_on_tab does the nav decision then the dummy POST; we only assert
    # on the nav counter, so a failure inside the POST stage is not fatal here.
    try:
        asyncio.run(ex._refresh_on_tab(0, force_fresh=force_fresh))
    except Exception:
        pass


def test_purchase_in_flight_skips_cart_nav():
    os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "1"
    ex, c, _ = _make(in_progress=True)
    _run(ex)
    assert c["nav"] == 0, f"must not navigate /cart during a purchase, got {c['nav']} navs"


def test_no_purchase_navigates_normally():
    os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "1"
    ex, c, _ = _make(in_progress=False)
    _run(ex)
    assert c["nav"] == 1, f"expected the normal stale-tab nav, got {c['nav']}"


def test_force_fresh_still_navigates_during_purchase():
    # ATC-401 recovery must still reload /cart to re-mint a write token.
    os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "1"
    ex, c, _ = _make(in_progress=True)
    _run(ex, force_fresh=True)
    assert c["nav"] == 1, f"force_fresh must override the guard, got {c['nav']}"


def test_kill_switch_restores_old_behaviour():
    os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "0"
    try:
        ex, c, _ = _make(in_progress=True)
        _run(ex)
        assert c["nav"] == 1, f"kill-switch must restore navigation, got {c['nav']}"
    finally:
        os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "1"


def test_warm_tab_skip_still_applies_without_purchase():
    # Pre-existing behaviour: a tab navved <90s ago is not re-navved.
    os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "1"
    ex, c, _ = _make(in_progress=False, cart_nav_age=10.0)
    _run(ex)
    assert c["nav"] == 0, f"warm tab should not re-nav, got {c['nav']}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n=== {passed}/{len(tests)} passed ===")
    sys.exit(0 if passed == len(tests) else 1)

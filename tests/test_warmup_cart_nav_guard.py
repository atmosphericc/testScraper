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
        if "readyState" not in script:
            self._c["dummy"] += 1          # the warmup dummy POST
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


# ── 2026-09-16 hot-sku plan P1/P3/P4: won-cart quiet warmups ────────────────
# 09-16: before every legacy re-shoot a forced re-warm loaded /cart on the
# holding account, whose page JS fired `PUT cart ADDRESSES` on the held cart.
# TARGET_HOLD_QUIET_WARMUP=1 skips the /cart nav (even force_fresh) while a won
# cart is held: the legacy ride (_won_cart_ride_until), the won-cart loop
# (_woncart_active_until) or a WC-3 marker younger than its TTL. Level 2
# (experimental, not armed) also skips the dummy POST.
_QUIET_FLAGS = ("TARGET_HOLD_QUIET_WARMUP", "TARGET_HELD_CART_TTL_S")


def _quiet_clear():
    for k in _QUIET_FLAGS:
        os.environ.pop(k, None)
    os.environ["TARGET_WARMUP_PAUSE_DURING_PURCHASE"] = "1"


def _run_ret(ex, force_fresh=False):
    return asyncio.run(ex._refresh_on_tab(0, force_fresh=force_fresh))


def test_quiet_level1_ride_skips_forced_nav_but_fires_dummy():
    import time as _t
    _quiet_clear()
    os.environ["TARGET_HOLD_QUIET_WARMUP"] = "1"
    try:
        ex, c, _ = _make(in_progress=False)
        ex._won_cart_ride_until = _t.time() + 60
        _run(ex, force_fresh=True)
        assert c["nav"] == 0 and c["dummy"] == 1, f"nav={c['nav']} dummy={c['dummy']}"
    finally:
        _quiet_clear()


def test_quiet_level0_ride_unchanged_force_fresh_navigates():
    import time as _t
    _quiet_clear()
    try:
        ex, c, _ = _make(in_progress=True)
        ex._won_cart_ride_until = _t.time() + 60
        _run(ex, force_fresh=True)
        assert c["nav"] == 1 and c["dummy"] == 1, f"nav={c['nav']} dummy={c['dummy']}"
    finally:
        _quiet_clear()


def test_quiet_level1_expired_ride_navigates():
    import time as _t
    _quiet_clear()
    os.environ["TARGET_HOLD_QUIET_WARMUP"] = "1"
    try:
        ex, c, _ = _make(in_progress=False)
        ex._won_cart_ride_until = _t.time() - 1
        _run(ex, force_fresh=True)
        assert c["nav"] == 1, f"nav={c['nav']}"
    finally:
        _quiet_clear()


def test_quiet_level2_skips_dummy_post_too():
    import time as _t
    _quiet_clear()
    os.environ["TARGET_HOLD_QUIET_WARMUP"] = "2"
    try:
        ex, c, _ = _make(in_progress=False)
        ex._won_cart_ride_until = _t.time() + 60
        r = _run_ret(ex, force_fresh=True)
        assert c["nav"] == 0 and c["dummy"] == 0 and r is False, f"nav={c['nav']} dummy={c['dummy']} r={r}"
        ex, c, _ = _make(in_progress=False)
        ex._cached_cart_headers = {"x": "1"}
        ex._won_cart_ride_until = _t.time() + 60
        assert _run_ret(ex) is True and c["dummy"] == 0
    finally:
        _quiet_clear()


def test_quiet_level_parse():
    from src.session.purchase_executor import hold_quiet_warmup_level
    _quiet_clear()
    try:
        got = {}
        for v in (None, "0", "1", " 1 ", "2", "3", "-1", "x", ""):
            if v is None:
                os.environ.pop("TARGET_HOLD_QUIET_WARMUP", None)
            else:
                os.environ["TARGET_HOLD_QUIET_WARMUP"] = v
            got[v] = hold_quiet_warmup_level()
        assert got == {None: 0, "0": 0, "1": 1, " 1 ": 1, "2": 2, "3": 0, "-1": 0, "x": 0, "": 0}, got
    finally:
        _quiet_clear()


def test_won_cart_loop_quiet_skips_nav_without_the_flag():
    import time as _t
    _quiet_clear()
    try:
        ex, c, _ = _make(in_progress=False)
        ex._woncart_active_until = _t.time() + 60
        _run(ex, force_fresh=True)
        assert c["nav"] == 0 and c["dummy"] == 1, f"nav={c['nav']} dummy={c['dummy']}"
    finally:
        _quiet_clear()


def test_held_marker_quiet_respects_ttl():
    import time as _t
    _quiet_clear()
    try:
        ex, c, _ = _make(in_progress=False)
        ex._held_cart = {"tcin": "1010892069", "created": _t.time() - 10}
        _run(ex, force_fresh=True)
        assert c["nav"] == 0, f"fresh marker: nav={c['nav']}"
        ex, c, _ = _make(in_progress=False)
        ex._held_cart = {"tcin": "1010892069", "created": _t.time() - 1000}
        _run(ex, force_fresh=True)
        assert c["nav"] == 1, f"expired marker (900 s TTL): nav={c['nav']}"
        os.environ["TARGET_HELD_CART_TTL_S"] = "2000"
        ex, c, _ = _make(in_progress=False)
        ex._held_cart = {"tcin": "1010892069", "created": _t.time() - 1000}
        _run(ex, force_fresh=True)
        assert c["nav"] == 0, f"TTL knob: nav={c['nav']}"
    finally:
        _quiet_clear()


# ── plan P4 (WC-2 item 3): the fleet cycle-warm is skipped while stock is in ──
def _mgr_stub():
    import threading
    import types
    from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager
    m = object.__new__(BulletproofPurchaseManager)
    m._state_lock = threading.Lock()
    m._load_states_unsafe = lambda: {}
    m._save_states_unsafe = lambda s: None
    m._active_purchases = {}
    m._warmup_cycle_counter = 0
    queued = []
    ex = types.SimpleNamespace(_cached_cart_headers_ts=0, _warmup_in_progress=False,
                               warm_shape_headers=lambda: "warm-coro")
    w = types.SimpleNamespace(purchase_executor=ex, label=lambda: "w1",
                              run_async=lambda coro: queued.append(coro))
    m.worker_pool = types.SimpleNamespace(workers=[w])

    class _Stop(Exception):
        pass

    def _sentinel():
        raise _Stop()

    m._maybe_run_session_sentinel = _sentinel
    return m, queued, _Stop


def _cycle(stock, flag):
    import contextlib
    import io
    if flag is None:
        os.environ.pop("TARGET_WARMUP_CYCLE_SKIP_ON_STOCK", None)
    else:
        os.environ["TARGET_WARMUP_CYCLE_SKIP_ON_STOCK"] = flag
    m, queued, stop = _mgr_stub()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                m.process_stock_data(stock)
            except stop:
                pass
    finally:
        os.environ.pop("TARGET_WARMUP_CYCLE_SKIP_ON_STOCK", None)
    return queued, buf.getvalue()


def test_cycle_warm_skipped_when_stock_is_in():
    live = {"1010892069": {"in_stock": True}, "1011407490": {"in_stock": False}}
    dead = {"1010892069": {"in_stock": False}}
    q, out = _cycle(live, None)
    assert q == ["warm-coro"], f"default must queue the warm: {q}"
    q, out = _cycle(live, "0")
    assert q == ["warm-coro"], f"flag 0 must queue the warm: {q}"
    q, out = _cycle(live, "1")
    assert q == [] and "skipped — stock is live" in out, f"flag 1 + stock in: {q} {out[-200:]}"
    q, out = _cycle(dead, "1")
    assert q == ["warm-coro"], f"flag 1 + no stock: {q}"
    q, out = _cycle({"1010892069": "garbage"}, "1")
    assert q == ["warm-coro"], f"malformed row never skips: {q}"


# ── R1 review (PC-3, 2026-09-17): the two other /cart loads during a hold ─────
# (a) _ensure_warmup_tab re-opened the warmup tab straight on /cart (e.g. after
#     a CHROME-AGE relaunch while a WC-3 cart was held);
# (b) the background token repair navigated the repaired tab back to /cart.
# Both now follow the nav rule above: held marker / won-cart loop at any
# level, the legacy ride with TARGET_HOLD_QUIET_WARMUP>=1.
class _OpenBrowser:
    def __init__(self):
        self.opened = []

    async def get(self, url, new_tab=False):
        self.opened.append(url)
        return type("T", (), {"url": url})()


def _open_ex():
    ex = object.__new__(PurchaseExecutor)
    br = _OpenBrowser()
    ex.session_manager = type("SM", (), {"browser": br})()
    ex._warmup_browser_ref = br
    ex._warmup_tabs = [None]
    ex._warmup_pool_size = 1
    ex._warmup_tab_cart_ts = {}

    async def _setup(tab, persistent=False):
        return None

    ex._setup_cdp_fetch_interceptor = _setup
    return ex, br


def _open(ex):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tab = asyncio.run(ex._ensure_warmup_tab(0))
    return tab, buf.getvalue()


def test_pc3_warmup_tab_opens_on_homepage_while_held():
    import time as _t
    _quiet_clear()
    try:
        ex, br = _open_ex()
        tab, _ = _open(ex)
        assert br.opened == ["https://www.target.com/cart"] and 0 in ex._warmup_tab_cart_ts, br.opened
        ex, br = _open_ex()
        ex._held_cart = {"tcin": "1010892069", "created": _t.time() - 10}
        tab, out = _open(ex)
        assert br.opened == ["https://www.target.com/"], br.opened
        assert 0 not in ex._warmup_tab_cart_ts and ex._warmup_tabs[0] is tab, ex._warmup_tab_cart_ts
        assert "on the homepage" in out and "(held)" in out, out
        ex, br = _open_ex()
        ex._held_cart = {"tcin": "1010892069", "created": _t.time() - 1000}   # expired (900 s)
        _open(ex)
        assert br.opened == ["https://www.target.com/cart"], br.opened
        ex, br = _open_ex()
        ex._woncart_active_until = _t.time() + 60
        _open(ex)
        assert br.opened == ["https://www.target.com/"], br.opened
        # The legacy ride only with TARGET_HOLD_QUIET_WARMUP>=1 (same as the nav rule).
        ex, br = _open_ex()
        ex._won_cart_ride_until = _t.time() + 60
        _open(ex)
        assert br.opened == ["https://www.target.com/cart"], br.opened
        os.environ["TARGET_HOLD_QUIET_WARMUP"] = "1"
        ex, br = _open_ex()
        ex._won_cart_ride_until = _t.time() + 60
        _open(ex)
        assert br.opened == ["https://www.target.com/"], br.opened
    finally:
        _quiet_clear()


class _RepairTab:
    def __init__(self, counters):
        self.url = "https://www.target.com/cart"
        self._c = counters
        self.navs = []

    async def get(self, url):
        self._c["nav"] += 1
        self.navs.append(url)
        return None

    async def evaluate(self, script, await_promise=False):
        if "readyState" in script:
            return "complete"
        self._c["dummy"] += 1
        return 401                        # heartbeat 401


def _repair_ex(held):
    import time as _t
    c = {"nav": 0, "dummy": 0, "repair": 0}
    ex = object.__new__(PurchaseExecutor)
    sm = _FakeSM(False)
    sm.account_id = "business"

    async def _fresh(tab=None, allow_nav=True, force=False):
        c["repair"] += 1
        return True

    sm.ensure_fresh_access_token = _fresh
    ex.session_manager = sm
    ex._warmup_pool_lock = asyncio.Lock()
    ex._cached_cart_headers = {"x": "1"}
    ex._cached_cart_headers_ts = 0.0
    ex._warmup_tabs = {0: None}
    ex._warmup_pool_size = 1
    ex._warmup_tab_cart_ts = {0: _t.time() - 999.0}
    ex._last_bg_token_repair_ts = 0.0
    ex._bg_token_repair_times = []
    ex._churn_alerted_ts = 0.0
    ex._atc_dead_token_midwindow_repair = True
    if held:
        ex._held_cart = {"tcin": "1010892069", "created": _t.time() - 10}
    tab = _RepairTab(c)

    async def fake_ensure(idx):
        return tab

    async def _confirm(t, idx, acct):
        return 401

    ex._ensure_warmup_tab = fake_ensure
    ex._confirm_write_auth_401 = _confirm
    return ex, c, tab


def test_pc3_token_repair_stays_off_cart_while_held():
    import contextlib
    import io
    _quiet_clear()
    try:
        ex, c, tab = _repair_ex(held=False)
        with contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(ex._refresh_on_tab(0))
        assert c["repair"] == 1 and tab.navs == ["https://www.target.com/cart"] * 2, (c, tab.navs)
        ex, c, tab = _repair_ex(held=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            asyncio.run(ex._refresh_on_tab(0))
        out = buf.getvalue()
        assert c["repair"] == 1 and tab.navs == [], (c, tab.navs)
        assert "token repaired — staying off /cart (won cart held: held)" in out, out[-400:]
    finally:
        _quiet_clear()


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

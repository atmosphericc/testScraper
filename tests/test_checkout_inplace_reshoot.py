#!/usr/bin/env python3
"""Unit test: in-place checkout re-shoot in PurchaseExecutor._place_order.

Guards the 2026-07-17 drop fix. When Target rejects the place-order POST with a
PRE-COMMIT throttle (HTTP 429 FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION / 424), the
item is still in the cart and no order was committed, so the executor re-fires
the place-order POST in place a few times (TARGET_CHECKOUT_INPLACE_RETRY_N,
default 4) instead of clearing the cart and re-racing the ATC 429 wall.

The load-bearing property is double-buy safety: it must re-fire ONLY while the
status stays 429/424, stop the instant an order_id lands, and NEVER re-fire again
after an ambiguous no-response (status==0) or any non-throttle status — those
defer to the pre-existing guards verbatim.

Drives the REAL _place_order on a bare instance with _api_place_order /
warm_shape_headers / DOM helpers mocked. No browser, no network.
Run: python tests/test_checkout_inplace_reshoot.py   (or: pytest tests/test_checkout_inplace_reshoot.py)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TARGET_API_PLACE_ORDER", "true")   # force API place-order path
os.environ["TARGET_CHECKOUT_INPLACE_DELAY_MIN"] = "0"     # no real waiting in tests
os.environ["TARGET_CHECKOUT_INPLACE_DELAY_MAX"] = "0"

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402


class _FakeTab:
    def __init__(self, url: str = "https://www.target.com/checkout/start"):
        self.url = url


def _R(status, reason="", success=False, order_id=None):
    return {"success": success, "status": status, "reason": reason,
            "order_id": order_id, "confirmation_url": None, "body": ""}


def _make_executor(seq, url_mutate=None, cvv_required=False):
    """seq = list of _api_place_order return dicts (last entry repeats).
    url_mutate = optional {api_call_index(1-based): new_url} to simulate an F5
    redirect mid-loop. Returns (executor, counters)."""
    ex = object.__new__(PurchaseExecutor)          # bypass heavy __init__
    ex.test_mode = False
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._cvv_required = cvv_required
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    c = {"api": 0}

    async def fake_api(tab):
        c["api"] += 1
        if url_mutate and c["api"] in url_mutate:
            tab.url = url_mutate[c["api"]]
        return seq[min(c["api"] - 1, len(seq) - 1)]

    async def fake_warm(force_fresh=False):
        return True

    async def fake_busy(tab):
        return False

    async def fake_find(tab):
        return (None, None)

    ex._api_place_order = fake_api
    ex.warm_shape_headers = fake_warm
    ex._handle_busy_modal = fake_busy
    ex._find_place_order_button = fake_find
    return ex, c


def _run(seq, n="4", url_mutate=None, url="https://www.target.com/checkout/start",
         cvv_required=False):
    os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = n
    ex, c = _make_executor(seq, url_mutate=url_mutate, cvv_required=cvv_required)
    r = asyncio.run(ex._place_order(_FakeTab(url)))
    return r, c["api"], ex._api_order_id


def test_first_shot_success_no_reshoot():
    r, api, oid = _run([_R(200, "ok", True, "OID-F")])
    assert r is True and api == 1 and oid == "OID-F"


def test_all_429_exhausts_four_reshoots():
    r, api, oid = _run([_R(429, "http_429")])
    assert r is False and api == 5 and oid is None      # 1 initial + 4 re-shoots


def test_all_424_exhausts_four_reshoots():
    r, api, _ = _run([_R(424, "http_424")])
    assert r is False and api == 5


def test_429_then_success_stops_early():
    r, api, oid = _run([_R(429, "http_429"), _R(200, "ok", True, "OID-B")])
    assert r is True and api == 2 and oid == "OID-B"


def test_429_then_no_response_never_refires_again():
    # The double-buy guard: status==0 is ambiguous (POST may have committed).
    # Must fire exactly 2 calls and defer to the no-response terminal guard.
    r, api, oid = _run([_R(429, "http_429"), _R(0, "fetch_threw:x")])
    assert r is False and api == 2 and oid is None


def test_429_then_reservation_defers_to_fastbail():
    r, api, _ = _run([_R(429, "http_429"), _R(409, "reservation_failure")])
    assert r is False and api == 2


def test_kill_switch_zero_is_one_shot():
    r, api, _ = _run([_R(429, "http_429")], n="0")
    assert r is False and api == 1


def test_off_checkout_redirect_stops_reshoot():
    # F5 bounces us to /account after the first re-shoot fire → guard stops.
    r, api, _ = _run([_R(429, "http_429")],
                     url_mutate={2: "https://www.target.com/account"})
    assert r is False and api == 2


# ── CVV challenge short-circuit (2026-07-21 regression) ─────────────────────
# 07-20→21 went 0-for-9 with 9 clean ATC 201s: every place-order POST came back
# 400 MISSING_CREDIT_CARD_CVV, and 5-for-5 the DOM recovery then hit 424
# RESERVATION_FAILURE because the doomed API shot burned ~1.0s of the window.

def test_cvv_latched_skips_api_entirely():
    # With the latch set, _place_order must NOT spend a shot on the API path —
    # it goes straight to DOM (which here finds no button and returns False).
    os.environ["TARGET_CVV_DOM_FIRST"] = "1"
    r, api, _ = _run([_R(200, "ok", True, "OID-X")], cvv_required=True)
    assert api == 0, f"expected 0 API calls when cvv latched, got {api}"
    assert r is False  # fake_find returns no button → DOM path can't complete


def test_cvv_kill_switch_restores_api_attempt():
    # TARGET_CVV_DOM_FIRST=0 must restore the old always-try-API behaviour.
    os.environ["TARGET_CVV_DOM_FIRST"] = "0"
    try:
        r, api, oid = _run([_R(200, "ok", True, "OID-Y")], cvv_required=True)
        assert api == 1 and r is True and oid == "OID-Y"
    finally:
        os.environ["TARGET_CVV_DOM_FIRST"] = "1"


def test_no_cvv_latch_still_uses_api():
    # Default path is unchanged when the challenge has never fired.
    os.environ["TARGET_CVV_DOM_FIRST"] = "1"
    r, api, oid = _run([_R(200, "ok", True, "OID-Z")], cvv_required=False)
    assert api == 1 and r is True and oid == "OID-Z"


def test_cvv_400_falls_through_to_dom_not_fastbail():
    # A 400 cvv_required must reach the DOM path (the only thing that can answer
    # the challenge) — it must NOT be swallowed by the 429/424 fast-bail branch.
    os.environ["TARGET_CVV_DOM_FIRST"] = "1"
    r, api, _ = _run([_R(400, "cvv_required")])
    # 400 is not a throttle status, so no re-shoots: exactly one API call, then DOM.
    assert api == 1, f"expected 1 API call, got {api}"
    assert r is False  # DOM stub has no button


def test_cvv_400_does_not_trigger_reshoot_loop():
    # Guard against a future edit adding 400 to the re-shoot status set: the
    # re-shoot loop is for pre-commit throttles only.
    os.environ["TARGET_CVV_DOM_FIRST"] = "1"
    _, api, _ = _run([_R(400, "cvv_required")], n="4")
    assert api == 1


# ── Dead-reservation bail (2026-07-21 regression) ───────────────────────────
# RESERVATION_FAILURE renders as Target's generic "busy" copy, so the DOM loop
# used to re-click Place Order into a reservation the server had already torn
# down (15 wasted re-clicks x ~1.5s on 07-20->21).

def _make_dom_executor(reject_reason="", busy=True):
    """Executor whose API path is disabled and whose DOM Place Order button
    exists, so the post-click wait loop actually runs. Counts Place Order clicks."""
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._cvv_required = True          # force DOM-first, skip API entirely
    ex._checkout_rejected = bool(reject_reason)
    ex._checkout_reject_reason = reject_reason
    ex._checkout_reject_status = 424 if reject_reason else 0
    c = {"clicks": 0, "busy": 0}

    async def fake_find(tab):
        return (object(), '[data-test="placeOrderButton"]')

    async def fake_scroll(btn):
        return None

    async def fake_click(btn):
        c["clicks"] += 1

    async def fake_cvv(tab):
        return True                  # CVV answered immediately

    async def fake_busy(tab):
        # _place_order dismisses a busy banner ONCE before the first click; only
        # calls made after a click can drive the wasteful re-click loop.
        if c["clicks"] > 0:
            c["busy"] += 1
        return busy                  # Target's generic busy copy is showing

    async def fake_stock(tab):
        return False

    async def fake_shot(tab, path):
        return None

    ex._find_place_order_button = fake_find
    ex._scroll_into_view = fake_scroll
    ex._dispatch_click = fake_click
    ex._handle_cvv_modal = fake_cvv
    ex._handle_busy_modal = fake_busy
    ex._handle_stock_error_modal = fake_stock
    ex._screenshot = fake_shot
    return ex, c


def test_reservation_failure_bails_without_reclicking():
    os.environ["TARGET_RESERVATION_BAIL"] = "1"
    ex, c = _make_dom_executor(reject_reason="RESERVATION_FAILURE")
    r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout")))
    assert r is False
    assert c["clicks"] == 1, f"expected exactly 1 click then bail, got {c['clicks']}"
    assert c["busy"] == 0, "must bail before the post-click busy handler claims it"


def test_reservation_bail_kill_switch_restores_reclicks():
    os.environ["TARGET_RESERVATION_BAIL"] = "0"
    try:
        ex, c = _make_dom_executor(reject_reason="RESERVATION_FAILURE")
        asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout")))
        assert c["clicks"] == 3, f"pre-07-21 behavior is 3 clicks, got {c['clicks']}"
    finally:
        os.environ["TARGET_RESERVATION_BAIL"] = "1"


def test_no_reservation_failure_leaves_busy_retry_intact():
    # Unrelated busy banner (no RESERVATION_FAILURE on the wire) must still use
    # the original retry-the-click path.
    os.environ["TARGET_RESERVATION_BAIL"] = "1"
    ex, c = _make_dom_executor(reject_reason="")
    asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout")))
    assert c["clicks"] == 3, f"expected the 3-attempt busy retry, got {c['clicks']}"


def test_fast_selling_hold_keeps_cart_and_reshoots():
    """2026-07-28 fix: FS rejection → hold through the cooldown IN PLACE (cart
    intact) → the existing re-shoot loop fires into the reopened window. That
    night the old zero-re-shoot path cleared 4 won carts into a 0-for-~250 ATC
    re-race."""
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "0.1"
    try:
        os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
        ex, c = _make_executor([_R(429, "http_429"), _R(200, "ok", True, "OID-H")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        assert r is True and c["api"] == 2 and ex._api_order_id == "OID-H", \
            f"r={r} api={c['api']} oid={ex._api_order_id}"
    finally:
        os.environ.pop("TARGET_FAST_SELLING_COOLDOWN_S", None)


def test_fast_selling_rehold_bounded_by_cycles():
    """2026-09-11 (09-11 drop 0-for): the only ATC 201 of the drop was held
    once, re-shot once, hit FS again and was CLEARED while the item stayed in
    stock 11+ min. Now a further FS re-HOLDS the won cart, bounded by
    TARGET_FAST_SELLING_HOLD_CYCLES (default 2 holds) — still no infinite loop.
    Sequence at default: shot FS → hold#1 → re-shoot FS → hold#2 → re-shoot FS
    → cycles spent → bail. 3 API shots, cart never cleared mid-budget."""
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "0.1"
    try:
        os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
        os.environ.pop("TARGET_FAST_SELLING_HOLD_CYCLES", None)  # default = 2
        os.environ.pop("TARGET_FAST_SELLING_HOLD_TOTAL_S", None)
        ex, c = _make_executor([_R(429, "http_429")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        assert r is False and c["api"] == 3, f"r={r} api={c['api']}"
    finally:
        os.environ.pop("TARGET_FAST_SELLING_COOLDOWN_S", None)


def test_fast_selling_hold_cycles_kill_switch_restores_one_hold():
    """TARGET_FAST_SELLING_HOLD_CYCLES=1 == exact prior behaviour: one hold,
    the second FS rejection stops the loop and defers to clear+re-race."""
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "0.1"
    os.environ["TARGET_FAST_SELLING_HOLD_CYCLES"] = "1"
    try:
        os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
        ex, c = _make_executor([_R(429, "http_429")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        # initial shot FS → one hold → re-shoot #1 FS again → loop stops → bail
        assert r is False and c["api"] == 2, f"r={r} api={c['api']}"
    finally:
        os.environ.pop("TARGET_FAST_SELLING_COOLDOWN_S", None)
        os.environ.pop("TARGET_FAST_SELLING_HOLD_CYCLES", None)


def test_fast_selling_rehold_stops_when_budget_spent():
    """A spent wall-clock budget (TARGET_FAST_SELLING_HOLD_TOTAL_S) must stop
    re-holding even with cycles left — this is what keeps the executor under
    the manager's future.result(timeout=150) so it can never be finalized and
    re-raced mid-flight."""
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "0.1"
    os.environ["TARGET_FAST_SELLING_HOLD_CYCLES"] = "5"
    os.environ["TARGET_FAST_SELLING_HOLD_TOTAL_S"] = "0"
    try:
        os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
        ex, c = _make_executor([_R(429, "http_429")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        assert r is False and c["api"] == 2, f"r={r} api={c['api']}"
    finally:
        os.environ.pop("TARGET_FAST_SELLING_COOLDOWN_S", None)
        os.environ.pop("TARGET_FAST_SELLING_HOLD_CYCLES", None)
        os.environ.pop("TARGET_FAST_SELLING_HOLD_TOTAL_S", None)


# ── 2026-09-11 won-cart ride (executor side) ─────────────────────────────────
def _ride_env(ride, cycles, total_s):
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "0.1"
    os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
    os.environ["TARGET_WON_CART_RIDE"] = ride
    os.environ["TARGET_FAST_SELLING_HOLD_CYCLES"] = cycles
    os.environ["TARGET_FAST_SELLING_HOLD_TOTAL_S"] = total_s


def _ride_env_clear():
    for k in ("TARGET_FAST_SELLING_COOLDOWN_S", "TARGET_WON_CART_RIDE",
              "TARGET_FAST_SELLING_HOLD_CYCLES", "TARGET_FAST_SELLING_HOLD_TOTAL_S"):
        os.environ.pop(k, None)


def test_won_cart_ride_extends_cycles_and_signals_manager():
    """RIDE=1: 6 holds ride out FS forever (shot + 6 re-shoots = 7), RETRY_N=4
    does NOT cap it (widened on the FS first shot), and the executor publishes
    a ride deadline so the manager extends its 150 s wait."""
    _ride_env("1", "6", "270")
    try:
        ex, c = _make_executor([_R(429, "http_429")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        ru = getattr(ex, "_won_cart_ride_until", 0.0)
        assert r is False and c["api"] == 7 and ru > 0, f"r={r} api={c['api']} ride_until={ru}"
    finally:
        _ride_env_clear()


def test_won_cart_ride_off_never_signals_and_keeps_retry_cap():
    """RIDE=0: no manager extension is possible, so the executor must never
    publish a ride deadline; RETRY_N=4 keeps its cap (shot + 4 re-shoots)."""
    _ride_env("0", "6", "270")
    try:
        ex, c = _make_executor([_R(429, "http_429")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        ru = getattr(ex, "_won_cart_ride_until", 0.0)
        assert r is False and c["api"] == 5 and ru == 0.0, f"r={r} api={c['api']} ride_until={ru}"
    finally:
        _ride_env_clear()


def test_won_cart_ride_timeout_helper_never_shortens_and_caps():
    """_extend_purchase_timeout: never earlier than start+140 s, never later
    than start+RIDE_MAX_S-10 s, no-op (0.0) with no purchase in flight."""
    import time as _t
    os.environ["TARGET_WON_CART_RIDE_MAX_S"] = "300"
    try:
        ex, _ = _make_executor([_R(200, "ok", True, "OID")])
        seen = {}

        class _Ctx:
            def reschedule(self, when):
                seen["when"] = when

        async def _go():
            loop = asyncio.get_running_loop()
            start = _t.time()
            ex._execute_started_at = start
            ex._purchase_timeout_ctx = _Ctx()
            t1 = ex._extend_purchase_timeout(start + 50)      # too early → 140
            d1 = seen["when"] - loop.time()
            t2 = ex._extend_purchase_timeout(start + 10_000)  # too late → 290
            d2 = seen["when"] - loop.time()
            ex._purchase_timeout_ctx = None
            t3 = ex._extend_purchase_timeout(start + 200)     # nothing in flight
            return t1 - start, d1, t2 - start, d2, t3

        a, d1, b, d2, t3 = asyncio.run(_go())
        assert abs(a - 140) < 1 and abs(d1 - 140) < 2, f"never-shorter: {a} {d1}"
        assert abs(b - 290) < 1 and abs(d2 - 290) < 2, f"cap: {b} {d2}"
        assert t3 == 0.0, f"no-op expected, got {t3}"
    finally:
        os.environ.pop("TARGET_WON_CART_RIDE_MAX_S", None)


def test_fast_selling_hold_kill_switch_restores_bail():
    """TARGET_FAST_SELLING_HOLD_CART=0 restores the 07-21 zero-re-shoot bail."""
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "5"
    os.environ["TARGET_FAST_SELLING_HOLD_CART"] = "0"
    try:
        os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
        ex, c = _make_executor([_R(429, "http_429")])
        ex._checkout_reject_status = 429
        ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        r = asyncio.run(ex._place_order(_FakeTab("https://www.target.com/checkout/start")))
        assert r is False and c["api"] == 1, f"r={r} api={c['api']}"
    finally:
        os.environ.pop("TARGET_FAST_SELLING_COOLDOWN_S", None)
        os.environ.pop("TARGET_FAST_SELLING_HOLD_CART", None)


# ── 2026-09-16 hot-sku plan P4 (WC-2): legacy hygiene + ride clean exit ─────
_FS_KEY = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
_WC2_FLAGS = ("TARGET_RESHOOT_FORCE_REWARM", "TARGET_WON_CART_RIDE_CLEAN_EXIT",
              "TARGET_WON_CART_RIDE_MAX_S", "TARGET_WONCART_DIRECT")


def _wc2_clear():
    for k in _WC2_FLAGS:
        os.environ.pop(k, None)
    _ride_env_clear()


def test_reshoot_force_rewarm_flag_values():
    """TARGET_RESHOOT_FORCE_REWARM: unset/'1' = the forced /cart re-warm before
    each in-place re-shoot (unchanged); '0' (bat) = a normal warm, so the
    purchase-time /cart guard applies. Any value other than '1' is off."""
    seen_all = {}
    for val, want in ((None, True), ("1", True), (" 1 ", True), ("0", False), (" 0 ", False), ("junk", False)):
        _wc2_clear()
        if val is not None:
            os.environ["TARGET_RESHOOT_FORCE_REWARM"] = val
        try:
            os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = "4"
            ex, c = _make_executor([_R(429, "http_429"), _R(200, "ok", True, "OID-W")])
            seen = []

            async def rec(force_fresh=False, _s=seen):
                _s.append(force_fresh)
                return True

            ex.warm_shape_headers = rec
            r = asyncio.run(ex._place_order(_FakeTab()))
            seen_all[val] = (r, c["api"], seen)
            assert r is True and c["api"] == 2 and seen == [want], f"val={val!r}: r={r} api={c['api']} seen={seen}"
        finally:
            _wc2_clear()


def _clamp_run(clean_exit, seq, first_rej=None, start_ago=10.0):
    """Run _place_order with FS holds recorded (never slept)."""
    import time as _t
    _ride_env("1", "6", "270")
    os.environ["TARGET_WON_CART_RIDE_CLEAN_EXIT"] = clean_exit
    os.environ["TARGET_WON_CART_RIDE_MAX_S"] = "300"
    try:
        ex, c = _make_executor(seq)
        if first_rej:
            ex._checkout_reject_status, ex._checkout_reject_reason = first_rej
        ex._execute_started_at = _t.time() - start_ago
        holds = []

        async def hold(label, max_s=None):
            holds.append((label, max_s, _t.time()))
            return True

        ex._hold_cart_for_fast_selling = hold
        r = asyncio.run(ex._place_order(_FakeTab()))
        return r, c["api"], holds, ex._won_cart_ride_until - _t.time()
    finally:
        _wc2_clear()


def test_ride_clean_exit_clamps_hold_budget_first_fs():
    """5a, FS on the first shot: with the flag the hold budget ends at
    start + RIDE_MAX - 60 (here ~230 s left instead of ~270 s); the ride
    deadline itself is untouched (ride ~ start + 290 either way)."""
    fs_rej = (429, _FS_KEY)
    out = {}
    for flag in ("1", "0"):
        r, api, holds, ride_left = _clamp_run(flag, [_R(429, "http_429"), _R(429, "http_429"),
                                                     _R(200, "ok", True, "OID-C")], first_rej=fs_rej)
        h2 = [h for h in holds if h[0] == "post-rejection#2"]
        out[flag] = (r, api, h2, ride_left)
        assert r is True and api == 3 and len(h2) == 1, f"flag={flag}: r={r} api={api} holds={holds}"
        assert 285 <= ride_left <= 291, f"flag={flag}: the ride keeps its deadline, left={ride_left:.1f}"
    assert 225 <= out["1"][2][0][1] <= 231, f"clamped budget: {out['1'][2]}"
    assert 265 <= out["0"][2][0][1] <= 271, f"unclamped budget: {out['0'][2]}"


def test_ride_clean_exit_clamps_hold_budget_mid_loop_fs():
    """5a, FS first seen mid-loop (the second clamp site)."""
    fs_body = dict(_R(429, "http_429"), body=_FS_KEY)
    for flag, lo, hi in (("1", 225, 231), ("0", 265, 271)):
        r, api, holds, ride_left = _clamp_run(flag, [_R(429, "http_429"), fs_body,
                                                     _R(200, "ok", True, "OID-M")])
        h1 = [h for h in holds if h[0] == "post-rejection#1"]
        assert r is True and api == 3 and len(h1) == 1, f"flag={flag}: r={r} api={api} holds={holds}"
        assert lo <= h1[0][1] <= hi, f"flag={flag}: budget={h1[0][1]}"
        assert 285 <= ride_left <= 291, f"flag={flag}: ride left={ride_left:.1f}"


def test_ride_clean_exit_clamp_past_cap_stops_holding():
    """A purchase already past start+RIDE_MAX-60 gets no further FS hold."""
    fs_body = dict(_R(429, "http_429"), body=_FS_KEY)
    r, api, holds, _ = _clamp_run("1", [_R(429, "http_429"), fs_body, _R(200, "ok", True, "OID-P")],
                                  start_ago=250.0)
    assert r is False and api == 2 and not [h for h in holds if h[0].startswith("post-rejection#")], \
        f"r={r} api={api} holds={holds}"


def _hang_run(ride_rel, clean_exit, po_inflight=False):
    """execute_purchase whose impl hangs past a 0.05 s timeout."""
    import time as _t
    import types
    import src.session.purchase_executor as pe_mod
    _wc2_clear()
    if clean_exit is not None:
        os.environ["TARGET_WON_CART_RIDE_CLEAN_EXIT"] = clean_exit
    ex, _ = _make_executor([_R(200, "ok", True, "X")])
    ex._purchase_timeout_ctx = None
    ex._note_atc_gate_outcome = lambda t, r: None
    ex._fastlane_placed = False
    ex._won_cart_ride_until = 0.0

    async def impl(tcin, quantity=1):
        ex._po_inflight = po_inflight
        ex._po_ambiguous = False
        if ride_rel is not None:
            ex._won_cart_ride_until = _t.time() + ride_rel
        await asyncio.sleep(5)

    ex._execute_purchase_impl = impl
    real = pe_mod.asyncio
    shim = types.SimpleNamespace(**{n: getattr(real, n) for n in dir(real) if not n.startswith("__")})
    shim.timeout = lambda s: real.timeout(0.05)
    pe_mod.asyncio = shim
    try:
        async def go():
            ex._page_lock = real.Lock()
            return await ex.execute_purchase("1010892069", quantity=2)
        return asyncio.run(go())
    finally:
        pe_mod.asyncio = real
        _wc2_clear()


def test_ride_timeout_reports_clean_exit():
    """5b: a timeout while a ride is advertised -> won_cart_ride_timeout, no
    'websocket' text (so the manager does not restart the browser)."""
    r = _hang_run(100.0, "1")
    assert r.get("reason") == "won_cart_ride_timeout" and r.get("success") is False, r
    assert "websocket" not in str(r).lower() and "1011" not in str(r), r
    assert r.get("ambiguous_commit") is False, r
    r = _hang_run(100.0, "1", po_inflight=True)
    assert r.get("reason") == "won_cart_ride_timeout" and r.get("ambiguous_commit") is True, r


def test_timeout_without_ride_is_still_impl_hang():
    """No ride -> the unchanged purchase_impl_hang (with 'websocket')."""
    r = _hang_run(None, "1")
    assert r.get("reason") == "purchase_impl_hang" and "websocket" in r.get("error", ""), r
    assert "ambiguous_commit" not in r, r      # AC-1 off -> the 11797839 dict


def test_ride_timeout_flag_off_unchanged():
    for flag in (None, "0"):
        r = _hang_run(100.0, flag)
        assert r.get("reason") == "purchase_impl_hang" and "websocket" in r.get("error", ""), (flag, r)


def test_sac_exhaust_line_prints_real_step_count():
    """Item 7 (log-only): the S&C exhaust line names the steps that ran."""
    import inspect
    from src.session.purchase_executor import PurchaseExecutor as _PE
    src = inspect.getsource(_PE._complete_payment)
    assert "exhausted after {_sac_steps_run} steps" in src and "_sac_steps_run = step + 1" in src
    assert "exhausted after 6 steps" not in src


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

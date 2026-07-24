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

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


def _make_executor(seq, url_mutate=None):
    """seq = list of _api_place_order return dicts (last entry repeats).
    url_mutate = optional {api_call_index(1-based): new_url} to simulate an F5
    redirect mid-loop. Returns (executor, counters)."""
    ex = object.__new__(PurchaseExecutor)          # bypass heavy __init__
    ex.test_mode = False
    ex._api_order_id = None
    ex._api_confirmation_url = None
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


def _run(seq, n="4", url_mutate=None, url="https://www.target.com/checkout/start"):
    os.environ["TARGET_CHECKOUT_INPLACE_RETRY_N"] = n
    ex, c = _make_executor(seq, url_mutate=url_mutate)
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

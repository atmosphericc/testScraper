#!/usr/bin/env python3
"""Unit test: place-order rejection classification from the `tgt-cart-error-key`
RESPONSE HEADER, and the CVV challenge latch.

Guards the 2026-07-21 drop fix. That night the bot detected 9/9 restocks and got
NINE clean ATC 201s -- the best add-to-cart night on record -- and bought zero.
Every place-order POST that reached Target came back:

    HTTP 400, header tgt-cart-error-key: MISSING_CREDIT_CARD_CVV, EMPTY body

The classifier only ever read the BODY, so it labelled this `http_400` and had no
idea CVV was the problem. It then fell into the slow DOM path, and 5-for-5 the
follow-up checkout POST came back 424 RESERVATION_FAILURE -- the ~1.0s the doomed
API shot cost was enough to lose the inventory reservation.

Load-bearing properties:
  1. A 400 whose CVV error key lives ONLY in the header classifies as
     `cvv_required` (not `http_400`), so the caller can react.
  2. The first such rejection latches `_cvv_required` and persists it, so the
     next run starts DOM-first instead of re-paying the 1.0s tax.
  3. The header is only trusted when its recorded status matches THIS response's
     status -- the re-shoot loop reuses the field across shots.
  4. A success is never misclassified by a stale header.

Drives the REAL _api_place_order with tab.evaluate mocked. No browser, no network.
Run: python tests/test_cvv_challenge_classify.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402


class _FakeTab:
    def __init__(self, url="https://www.target.com/checkout"):
        self.url = url
        self._resp = {"status": 200, "body": "{}"}

    async def evaluate(self, script, await_promise=False):
        return self._resp


def _make(status, body, hdr_key="", hdr_status=None):
    """Executor with the fetch result and interceptor-recorded header primed."""
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._cvv_required = False
    ex._cached_cart_headers = {"X-GyJwza5Z-a": "tok", "Accept": "application/json"}
    ex._cached_cart_headers_ts = __import__("time").time()
    ex._checkout_rejected = bool(hdr_key)
    ex._checkout_reject_reason = hdr_key
    # Default: interceptor saw the same status the fetch got (the normal case).
    ex._checkout_reject_status = status if hdr_status is None else hdr_status
    ex._persisted = []
    ex._persist_cvv_challenge_flag = lambda: ex._persisted.append(True)

    tab = _FakeTab()
    tab._resp = {"status": status, "body": body}
    return ex, tab


def _call(ex, tab):
    return asyncio.run(ex._api_place_order(tab))


# ── 1. Header-only CVV rejection is classified ──────────────────────────────

def test_cvv_header_on_empty_body_classifies_as_cvv_required():
    # The exact 07-21 shape: 400, empty-ish body, key only in the header.
    ex, tab = _make(400, "", hdr_key="MISSING_CREDIT_CARD_CVV")
    r = _call(ex, tab)
    assert r["reason"] == "cvv_required", f"got {r['reason']!r}"
    assert r["success"] is False


def test_cvv_rejection_latches_and_persists():
    ex, tab = _make(400, "", hdr_key="MISSING_CREDIT_CARD_CVV")
    _call(ex, tab)
    assert ex._cvv_required is True, "latch must be set for subsequent shots"
    assert ex._persisted, "latch must be persisted so it survives a restart"


def test_plain_400_without_header_stays_http_400():
    # No CVV key -> must NOT be mislabelled, and must NOT latch.
    ex, tab = _make(400, "some other error")
    r = _call(ex, tab)
    assert r["reason"] == "http_400", f"got {r['reason']!r}"
    assert ex._cvv_required is False


# ── 2. Other header keys still route correctly ──────────────────────────────

def test_reservation_failure_header_classifies():
    ex, tab = _make(424, "", hdr_key="RESERVATION_FAILURE")
    r = _call(ex, tab)
    assert r["reason"] == "reservation_failure", f"got {r['reason']!r}"
    assert ex._cvv_required is False


def test_body_based_classification_still_wins_when_present():
    # Body already says RESERVATION_FAILURE -> unchanged pre-07-21 behavior.
    ex, tab = _make(424, '{"message":"RESERVATION_FAILURE"}')
    r = _call(ex, tab)
    assert r["reason"] == "reservation_failure"


# ── 3. Staleness guard (the re-shoot loop reuses the field) ─────────────────

def test_stale_header_from_previous_shot_is_ignored():
    # Interceptor recorded a 424 CVV key from an earlier shot, but THIS fetch
    # got a 429. The stale key must not leak into this shot's classification.
    ex, tab = _make(429, "", hdr_key="MISSING_CREDIT_CARD_CVV", hdr_status=424)
    r = _call(ex, tab)
    assert r["reason"] == "http_429", f"stale header leaked: {r['reason']!r}"
    assert ex._cvv_required is False, "must not latch off a stale header"


def test_success_never_misclassified_by_stale_header():
    ex, tab = _make(200, '{"orders":[{"order_id":"OID-1","reference_id":"9"}]}',
                    hdr_key="MISSING_CREDIT_CARD_CVV", hdr_status=400)
    r = _call(ex, tab)
    assert r["success"] is True and r["order_id"] == "OID-1"
    assert ex._cvv_required is False


# ── 4. Env override on the persisted flag ───────────────────────────────────

def test_env_override_forces_and_clears_latch():
    ex = object.__new__(PurchaseExecutor)
    ex.session_manager = type("S", (), {"account_id": "primary"})()
    prev = os.environ.get("TARGET_CVV_REQUIRED")
    try:
        os.environ["TARGET_CVV_REQUIRED"] = "1"
        assert ex._load_cvv_challenge_flag() is True
        os.environ["TARGET_CVV_REQUIRED"] = "0"
        assert ex._load_cvv_challenge_flag() is False
    finally:
        if prev is None:
            os.environ.pop("TARGET_CVV_REQUIRED", None)
        else:
            os.environ["TARGET_CVV_REQUIRED"] = prev


def test_flag_file_roundtrip_seeds_next_run():
    ex = object.__new__(PurchaseExecutor)
    ex.session_manager = type("S", (), {"account_id": "utest-acct"})()
    os.environ.pop("TARGET_CVV_REQUIRED", None)
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        try:
            os.chdir(td)
            assert ex._load_cvv_challenge_flag() is False
            ex._persist_cvv_challenge_flag()
            assert ex._load_cvv_challenge_flag() is True, \
                "a fresh run must start DOM-first after a prior CVV challenge"
        finally:
            os.chdir(cwd)


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

#!/usr/bin/env python3
"""Smoke test: organic-dispatch ATC fallback on the Shape 401 lockout (2026-08-04).

08-04 restock forensics: primary and alt-1 got a RECEIVED 401 on EVERY
injected-header ATC for 9+ hours (~900 shots each, ~850 futile member-token
mints) while business converted 4 races — yet all three accounts' warmup-tab
dummy POSTs (plain fetch, NO injected headers) passed at ~92% all night. The
/cart page's Shape VM signs page-context fetches organically; the ring-replayed
headers are what the per-identity lockout keys on. The fallback fires the REAL
cart_items POST from a warmup tab with no injected headers, only after every
injected attempt in the execution returned a received 401 (definitive no-add ⇒
no double-ATC exposure).

Pins:
  - _native_atc_fallback real-method behavior on a stub warmup tab
    (201 passthrough, kill-switch, no-tab skip, evaluate failure, bad tcin,
    qty clamp, no injected Shape headers in the fired JS)
  - source contract: the call site is gated on fast_status2 == 401 and sets
    cart_confirmed on 200/201

No browser, no network. Run: python tests/test_native_atc_fallback.py
"""
from __future__ import annotations

import asyncio
import os
import re
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


class _WarmTab:
    """Stub warmup tab: records the JS it was asked to evaluate."""

    def __init__(self, result=201, raise_=False):
        self._result = result
        self._raise = raise_
        self.last_js = None
        self.url = "https://www.target.com/cart"

    async def evaluate(self, js, await_promise=False):
        self.last_js = js
        if self._raise:
            raise RuntimeError("cdp dead")
        return self._result


def _executor(tabs):
    ex = object.__new__(PurchaseExecutor)
    ex._warmup_tabs = tabs
    return ex


def _run(ex, tcin="94336414", qty=2):
    return asyncio.run(ex._native_atc_fallback(tcin, qty))


def test_organic_201_passthrough():
    tab = _WarmTab(result=201)
    check("organic_201_passthrough", _run(_executor([tab])) == 201)
    check("real_tcin_embedded", "94336414" in (tab.last_js or ""))
    check("qty_embedded", "quantity: 2" in (tab.last_js or ""))


def test_no_injected_headers_in_native_js():
    # The whole point: the page's Shape VM signs the fetch. The fired JS must
    # carry ONLY the three plain headers — no X-* Shape tokens, no ring
    # interpolation (extra_headers_js never reaches this JS).
    tab = _WarmTab(result=201)
    _run(_executor([tab]))
    js = tab.last_js or ""
    check("no_shape_x_headers", "X-" not in js)
    check("no_ring_spread", "...h," not in js and "extra_headers" not in js)
    for h in ("Content-Type", "Accept", "Origin"):
        check(f"plain_header_present: {h}", h in js)
    check("body_matches_real_atc_shape",
          "fulfillment_type: 'SHIPPING'" in js and "fulfillment_type_code: '02'" in js)


def test_kill_switch():
    os.environ["TARGET_ATC_NATIVE_FALLBACK"] = "0"
    try:
        tab = _WarmTab(result=201)
        check("kill_switch_returns_0", _run(_executor([tab])) == 0)
        check("kill_switch_fires_nothing", tab.last_js is None)
    finally:
        os.environ.pop("TARGET_ATC_NATIVE_FALLBACK", None)


def test_no_live_tab_skips():
    check("no_live_tab_skips", _run(_executor([None, None])) == 0)
    check("empty_tab_list_skips", _run(_executor([])) == 0)


def test_picks_first_live_tab():
    tab1 = _WarmTab(result=201)
    check("skips_none_picks_live", _run(_executor([None, tab1])) == 201)
    check("live_tab_was_used", tab1.last_js is not None)


def test_evaluate_failure_is_safe():
    check("evaluate_raise_returns_0", _run(_executor([_WarmTab(raise_=True)])) == 0)
    check("non_numeric_returns_0", _run(_executor([_WarmTab(result="huh")])) == 0)


def test_bad_tcin_rejected():
    tab = _WarmTab(result=201)
    check("script_tcin_rejected", _run(_executor([tab]), tcin="'};alert(1);//") == 0)
    check("bad_tcin_fires_nothing", tab.last_js is None)
    check("empty_tcin_rejected", _run(_executor([_WarmTab()]), tcin="") == 0)


def test_qty_clamped():
    tab = _WarmTab(result=201)
    _run(_executor([tab]), qty=9)
    check("qty_clamped_to_3", "quantity: 3" in (tab.last_js or ""))
    tab2 = _WarmTab(result=201)
    _run(_executor([tab2]), qty=0)
    check("qty_floor_1", "quantity: 1" in (tab2.last_js or ""))


def test_call_site_contract():
    # Fires ONLY in the all-received-401 state (fast_status2 == 401), never on
    # 429/timeout/0 — a non-401 terminal can be an unreceived response and a
    # second ATC could double-add.
    m = re.search(
        r"if fast_status2 == 401:\s*\n\s*_nat_status = await self\._native_atc_fallback\(tcin, quantity\)"
        r"[\s\S]{0,400}?if _nat_status in \(200, 201\):[\s\S]{0,400}?cart_confirmed = True",
        SRC)
    check("call_site_gated_on_received_401", m is not None)
    check("kill_switch_documented", "TARGET_ATC_NATIVE_FALLBACK" in SRC)


if __name__ == '__main__':
    test_organic_201_passthrough()
    test_no_injected_headers_in_native_js()
    test_kill_switch()
    test_no_live_tab_skips()
    test_picks_first_live_tab()
    test_evaluate_failure_is_safe()
    test_bad_tcin_rejected()
    test_qty_clamped()
    test_call_site_contract()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

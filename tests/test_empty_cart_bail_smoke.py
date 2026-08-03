#!/usr/bin/env python3
"""Smoke test: evicted-cart fast-bail (2026-08-02, 07-31 late-window forensics).

07-31 04:50-05:13: after each RESERVATION_FAILURE Target emptied the cart
server-side (items bumped to Saved-for-later); follow-up place-order POSTs
400'd with NO tgt-cart-error-key, the checkout page rendered "There are no
items in your cart right now.", and the SPA bounced tabs to /cart ("Your cart
is empty"). Neither phrase list matched, so every attempt burned a DOM click +
the 12s confirmation wait at a dead cart (~15-25s each).

Pins:
  - _cart_evicted_on_page phrase semantics (real method, stub tab)
  - source contract: TARGET_EMPTY_CART_BAIL gates all four bail sites and the
    evicted phrases stay in sync between the helper and the DIAGNOSIS chain
  - source contract: the re-shoot loop re-navs ONCE to /checkout when the SPA
    bounced the tab mid-hold (TARGET_RESHOOT_RENAV), instead of aborting with
    zero post-hold shots (07-31 03:44 hold bug)

No browser, no network. Run: python tests/test_empty_cart_bail_smoke.py
"""
from __future__ import annotations

import asyncio
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


class _Tab:
    def __init__(self, text=None, raise_=False):
        self._text = text
        self._raise = raise_
        self.url = "https://www.target.com/checkout"

    async def evaluate(self, _js):
        if self._raise:
            raise RuntimeError("cdp dead")
        return self._text


def evicted(tab) -> bool:
    return asyncio.run(PurchaseExecutor._cart_evicted_on_page(object.__new__(PurchaseExecutor), tab))


def test_checkout_empty_banner_detected():
    t = _Tab("Please review these errors\nThere are no items in your cart right now.\nCart $70.38 total")
    check("checkout_empty_banner_detected", evicted(t) is True)


def test_cart_page_empty_detected():
    t = _Tab("Hi, Eric\nYour cart is empty\nSaved for later (2)")
    check("cart_page_empty_detected", evicted(t) is True)


def test_healthy_checkout_not_flagged():
    t = _Tab("Checkout\nShipping address\nPayment\nPlace your order\nEst. total $70.38")
    check("healthy_checkout_not_flagged", evicted(t) is False)


def test_evaluate_failure_is_safe():
    check("evaluate_failure_is_safe", evicted(_Tab(raise_=True)) is False)
    check("none_body_is_safe", evicted(_Tab(None)) is False)


def test_flag_gates_all_bail_sites():
    # wire 400-no-key fast-bail, wait-loop probe, no-button diagnosis,
    # DIAGNOSIS-chain classification — each must consult the kill-switch.
    n = len(re.findall(r"TARGET_EMPTY_CART_BAIL", SRC))
    check("flag_gates_all_bail_sites (>=4 gates)", n >= 4)


def test_phrases_in_sync_helper_vs_diagnosis():
    # Both the helper and the page-text DIAGNOSIS chain must know both phrases.
    for phrase in ("no items in your cart", "your cart is empty"):
        check(f"phrase_present_twice: {phrase!r}",
              len(re.findall(re.escape(phrase), SRC)) >= 2)


def test_wire_bail_excludes_keyed_400():
    # The 400 fast-bail must require an EMPTY tgt-cart-error-key so a keyed 400
    # (e.g. MISSING_CREDIT_CARD_CVV) never takes it.
    m = re.search(r"status'\)\s*==\s*400\s*\n\s*and not \(self\._checkout_reject_reason", SRC)
    check("wire_bail_excludes_keyed_400", m is not None)


def test_reshoot_renav_contract():
    check("renav_flag_present", "TARGET_RESHOOT_RENAV" in SRC)
    check("renav_single_shot", "_renav_done = False" in SRC and "_renav_done = True" in SRC)
    check("renav_targets_checkout", 'tab.get("https://www.target.com/checkout")' in SRC)


if __name__ == '__main__':
    test_checkout_empty_banner_detected()
    test_cart_page_empty_detected()
    test_healthy_checkout_not_flagged()
    test_evaluate_failure_is_safe()
    test_flag_gates_all_bail_sites()
    test_phrases_in_sync_helper_vs_diagnosis()
    test_wire_bail_excludes_keyed_400()
    test_reshoot_renav_contract()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

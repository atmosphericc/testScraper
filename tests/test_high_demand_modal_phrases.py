#!/usr/bin/env python3
"""Wiring test: the "High-demand item in your cart" modal classifies as BUSY.

Guards the 2026-07-24 post-drop fix. That night converted 4 orders / 8 units
(fast lane 4-for-4), but at 02:24 alt-1's DOM fallback landed on a skeleton
/checkout page whose only content was a NEW Target throttle modal:

    High-demand item in your cart
    A popular item in your cart is causing a delay. We're managing high
    traffic right now. Please try again.                          [Ok]

Neither busy-phrase list matched that copy, so `_handle_busy_modal` returned
False and the post-mortem diagnosis fell through to "Unknown failure" →
`checkout_navigation_failed` (terminal) → cart cleared 32s in — while the item
stayed IN STOCK another ~18 minutes. A `checkout_busy_retryable` classification
would have re-raced it inside the window.

Load-bearing properties:
  1. The observed 07-24 modal copy matches BOTH phrase lists (the JS
     BUSY_PHRASES inside `_handle_busy_modal` and the Python page-text
     diagnosis list), exactly as each matcher applies them (lowercased
     substring match).
  2. The classic "Checkout is busy right now / limiting how many guests"
     copy still matches both lists (no regression while editing them).

Both lists live as inline literals at their use sites (no shared constant —
surgical-change rule), so this test extracts them from the source to catch a
future edit that fixes one list and misses the other.

No browser, no network. Run: python tests/test_high_demand_modal_phrases.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT / "src" / "session" / "purchase_executor.py"

# Verbatim copy from logs/checkout_no_place_order_20260724_022433.png (07-24 02:24:33).
HIGH_DEMAND_MODAL_TEXT = (
    "High-demand item in your cart\n"
    "A popular item in your cart is causing a delay. We're managing high "
    "traffic right now. Please try again.\n"
    "Ok"
).lower()

# The pre-07-24 modal both matchers were built for (06-30 checkout-busy fix).
CLASSIC_BUSY_MODAL_TEXT = (
    "Checkout is busy right now\n"
    "We're limiting how many guests can check out due to high demand. "
    "Please keep trying. You'll get through soon.\n"
    "Ok"
).lower()


def _extract_phrase_lists() -> list[list[str]]:
    """Both busy-phrase list literals, identified by their shared anchor phrase.

    Quoted strings are walked left-to-right with one alternation so a
    double-quoted phrase containing an apostrophe ("can't view") is consumed
    atomically instead of de-syncing the single-quote pairing.
    """
    src = SRC_PATH.read_text(encoding="utf-8")
    blobs = re.findall(r"\[[^\[\]]*?limiting how many guests[^\[\]]*?\]", src)
    lists = []
    for blob in blobs:
        blob = re.sub(r"//[^\n]*", "", blob)  # JS comments inside the literal
        phrases = [
            m.group(1) if m.group(1) is not None else m.group(2)
            for m in re.finditer(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'", blob)
        ]
        lists.append([p for p in phrases if p and len(p) > 2])
    return lists


def test_both_phrase_lists_present():
    lists = _extract_phrase_lists()
    assert len(lists) == 2, (
        f"expected exactly 2 busy-phrase lists (JS BUSY_PHRASES + Python "
        f"diagnosis list), found {len(lists)} — did one move or lose its "
        f"'limiting how many guests' anchor?"
    )


def test_high_demand_modal_matches_both_lists():
    for i, phrases in enumerate(_extract_phrase_lists()):
        assert any(p in HIGH_DEMAND_MODAL_TEXT for p in phrases), (
            f"phrase list #{i} does not match the 07-24 'High-demand item' "
            f"modal copy — that page state would again end the race as "
            f"'Unknown failure' instead of checkout_busy_retryable"
        )


def test_classic_busy_modal_still_matches_both_lists():
    for i, phrases in enumerate(_extract_phrase_lists()):
        assert any(p in CLASSIC_BUSY_MODAL_TEXT for p in phrases), (
            f"phrase list #{i} no longer matches the classic 'Checkout is "
            f"busy right now' modal copy — 06-30 regression"
        )


if __name__ == "__main__":
    test_both_phrase_lists_present()
    test_high_demand_modal_matches_both_lists()
    test_classic_busy_modal_still_matches_both_lists()
    print("[PASS] 3/3 — high-demand modal copy matches both busy-phrase lists")
    sys.exit(0)

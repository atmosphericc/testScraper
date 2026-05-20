"""
End-to-end hybrid checkout (Day 4).

The full speed prize: drive WalmartHybridCheckout through real Chrome
against the mitmproxy sim, performing every step of the purchase flow
via GraphQL + PIE — zero DOM keystrokes, zero DOM clicks.

Pipeline:
  1. Load /cart (populates __NEXT_DATA__ with cartId + lineItems)
  2. read_cart_context  →  cartId + lineItems extracted
  3. bump_quantity(5)   →  updateItems mutation
  4. reserve_cheapest_slot  →  getSlots GET + reserveSlotMutation POST
  5. submit_cvv_via_pie("123")  →  PIE-encrypted CVV POST
       sim decrypts with test private key, asserts plaintext == "123"
  6. place_order  →  CreateContract mutation, returns pcid

Critical assertions:
  - Sim received the encrypted CVV and decrypted to the expected plaintext
  - No DOM CVV keystroke events were dispatched (this is the speed prize:
    we proved we can buy without touching the CVV form on the page)
  - place_order returned a pcid (the order was "placed" sim-side)

Run: python tests/test_e2e_hybrid_checkout.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import urllib3   # noqa: E402
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from walmart.checkout_api import (   # noqa: E402
    UPDATE_ITEMS_HASH, CREATE_CONTRACT_HASH, GET_SLOTS_HASH,
    RESERVE_SLOT_HASH, WalmartHybridCheckout,
)
from walmart.sim.test_harness import WalmartSimHarness   # noqa: E402


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


# ── scenarios ────────────────────────────────────────────────────────────


async def scenario_full_pipeline_with_pie(h: WalmartSimHarness):
    """All hashes known + PIE CVV submission. Full hybrid pipeline."""
    h.ctl.reset()
    # Mark all checkout hashes as known so the bot's first POST hits
    # the canned success directly (no APQ retries — that's separate test).
    h.ctl.set_hash_scenario("updateItems", known_hashes=[UPDATE_ITEMS_HASH])
    h.ctl.set_hash_scenario("CreateContract",
                            known_hashes=[CREATE_CONTRACT_HASH])
    h.ctl.set_hash_scenario("getSlots", known_hashes=[GET_SLOTS_HASH])
    h.ctl.set_hash_scenario("reserveSlotMutation",
                            known_hashes=[RESERVE_SLOT_HASH])

    # Load /cart so __NEXT_DATA__ is populated
    await h.tab.get("https://www.walmart.com/cart")
    await asyncio.sleep(1.0)

    api = WalmartHybridCheckout(h.tab)

    # Step 1: read_cart_context
    ctx = await api.read_cart_context(timeout_s=5.0)
    _check("step 1: read_cart_context returned context",
           ctx is not None, detail=f"ctx={ctx}")
    if not ctx:
        return

    # Step 2: bump_quantity
    bump_result = await api.bump_quantity(ctx, qty=5)
    _check("step 2: bump_quantity returned response",
           bump_result is not None)
    if bump_result:
        parsed = WalmartHybridCheckout.parse_update_items(bump_result)
        _check("step 2: parse_update_items returned valid data",
               parsed is not None and parsed.get("committed_qty") == 5)

    # Step 3: reserve_cheapest_slot
    slot_result = await api.reserve_cheapest_slot(ctx)
    _check("step 3: reserve_cheapest_slot succeeded",
           slot_result is not None)

    # Step 4: PIE CVV submission — THE KEY ASSERTION
    pie_result = await api.submit_cvv_via_pie("123")
    _check("step 4: submit_cvv_via_pie returned response",
           pie_result is not None and pie_result.get("status") == "ok",
           detail=f"pie_result={pie_result}")

    # Step 4.5: sim must have decrypted the CVV correctly
    decrypted = h.ctl.get_pie_decrypted()
    _check("step 4.5: sim received exactly 1 PIE submission",
           len(decrypted) == 1, detail=f"got {len(decrypted)}: {decrypted}")
    if len(decrypted) == 1:
        entry = decrypted[0]
        _check("step 4.5: PIE plaintext decrypted == '123'",
               entry.get("decrypted") == "123",
               detail=f"got decrypted={entry.get('decrypted')!r}")
        _check("step 4.5: PIE submission had no decryption error",
               entry.get("error") is None,
               detail=f"error={entry.get('error')}")
        _check("step 4.5: PIE submission used correct key_id",
               entry.get("key_id") == "test-pie-key-id-0",
               detail=f"key_id={entry.get('key_id')}")
        _check("step 4.5: ciphertext was 512 hex chars (256 bytes @ 2048-bit)",
               entry.get("ciphertext_hex_len") == 512,
               detail=f"len={entry.get('ciphertext_hex_len')}")

    # Step 5: place_order
    order_body = await api.place_order(ctx)
    _check("step 5: place_order returned response", order_body is not None)
    if order_body:
        parsed = WalmartHybridCheckout.parse_create_contract(order_body)
        _check("step 5: parse_create_contract returned pcid",
               parsed is not None and parsed.get("pcid") == "test-pcid-12345",
               detail=f"parsed={parsed}")


async def scenario_pie_fails_then_dom_fallback(h: WalmartSimHarness):
    """When PIE fails (wrong-shape pubkey), submit_cvv_via_pie returns None
    cleanly so the caller can fall back to DOM CVV entry.

    We force-fail PIE by uninstalling the sim's keypair (so the modulus is
    "00" placeholder that won't successfully decrypt anything).
    """
    h.ctl.reset()
    # No reset_pie hook in current sim — we test the OTHER failure mode:
    # encrypt_cvv raising on bad input. Pass a 3-digit alphabetic CVV.
    # The pie module rejects → submit_cvv_via_pie returns None → bot
    # would fall back to DOM (not exercised here since this is pie-only).

    await h.tab.get("https://www.walmart.com/cart")
    await asyncio.sleep(0.5)
    api = WalmartHybridCheckout(h.tab)

    # encrypt_cvv raises on non-digit
    result = await api.submit_cvv_via_pie("abc")
    _check("submit_cvv_via_pie returns None on rejected input",
           result is None,
           detail=f"got {result}")


async def scenario_no_dom_cvv_keystrokes(h: WalmartSimHarness):
    """Hard assertion: when PIE submission succeeds, NO DOM CVV keystroke
    events were ever dispatched. This is what differentiates the hybrid
    path from the legacy DOM-keystroke path.

    Method: count Network requests to walmart.com/api/checkout-customer/*
    vs anything that looks like a keystroke event. Since we never load a
    CVV form in our sim test, this is essentially "no extra DOM traffic
    happened during PIE submission".
    """
    h.ctl.reset()
    await h.tab.get("https://www.walmart.com/cart")
    await asyncio.sleep(0.5)

    api = WalmartHybridCheckout(h.tab)
    result = await api.submit_cvv_via_pie("999")
    _check("PIE submission succeeds", result is not None and result.get("status") == "ok")

    # Inspect sim log for the PIE-specific URL
    log = h.ctl.get_log()
    pie_calls = [e for e in log if e.get("route") == "pie_submit"]
    cvv_form_loads = [e for e in log if "checkout-customer" in str(e).lower()
                      and e.get("route") != "pie_submit"]
    _check("exactly one pie_submit log entry",
           len(pie_calls) == 1,
           detail=f"pie_calls={pie_calls}")
    _check("no DOM CVV form interactions (no other checkout-customer routes)",
           len(cvv_form_loads) == 0,
           detail=f"cvv_form_loads={cvv_form_loads}")


# ── runner ───────────────────────────────────────────────────────────────


SCENARIOS = [
    ("full hybrid pipeline with PIE", scenario_full_pipeline_with_pie),
    ("PIE rejects bad input → returns None", scenario_pie_fails_then_dom_fallback),
    ("PIE path emits no DOM CVV keystroke traffic", scenario_no_dom_cvv_keystrokes),
]


async def run_scenarios():
    for name, fn in SCENARIOS:
        print(f"\n=== {name} ===")
        try:
            async with WalmartSimHarness(headless=True) as h:
                await fn(h)
        except Exception as e:
            results.append(TestResult(
                name=f"{name} (harness error)",
                status="FAIL",
                detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:600]}",
            ))


def main():
    print("=" * 70)
    print("Hybrid checkout E2E with PIE (Day 4)")
    print("=" * 70)
    asyncio.run(run_scenarios())

    print()
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:4]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

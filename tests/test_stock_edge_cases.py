"""
Stock detection edge cases — Day 6.

Walmart adapter's parse_response handles many response shapes. The existing
test_walmart_framework_unit.py covers the happy path (in-stock, OOS,
third-party, errors, queue). This file fills the remaining JS branches in
walmart_adapter.py:176-269:

  1. body_json=None (Walmart returns empty body)
  2. Malformed __NEXT_DATA__ (truncated JSON) — produces NO_NEXT_DATA error
  3. Missing sellerId AND sellerName (defensive: third-party-or-unknown)
  4. PRE_ORDER_SELLABLE + showAtc=true + walmart_direct → in_stock=True
  5. Marketplace seller WITH showAtc=true → in_stock=False
  6. Per-item HTTP_456 → is_blocked_response True (Akamai signature)
  7. Mixed-shape array (product OK + error + QUEUED) → 3 ItemStatus in order
  8. NO_PRODUCT error (page loaded but product field missing)
  9. price extraction edge cases (None price, non-numeric)
 10. Per-item HTTP_403 → blocked

Run: python tests/test_stock_edge_cases.py
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.stack.retailer_adapter import FetchResult, ItemStatus   # noqa: E402
from walmart.walmart_adapter import WALMART_SELLER_ID, WalmartAdapter   # noqa: E402


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


def _adapter() -> WalmartAdapter:
    return WalmartAdapter()


# ── tests ────────────────────────────────────────────────────────────────


def test_body_json_none_returns_empty_list():
    """When the fetch JS returned a result but body_json is None (or unparseable),
    parse_response must return [] cleanly — not crash, not emit phantom items."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json=None)
    out = a.parse_response(fr, ["X1"])
    _check("body_json=None → empty list", out == [],
           detail=f"got {out}")


def test_no_next_data_error():
    """JS template emits error='NO_NEXT_DATA' when the HTML response didn't
    contain a __NEXT_DATA__ script tag (e.g., Walmart served an error page
    that still 200'd)."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "error": "NO_NEXT_DATA", "ms": 220, "http_status": 200,
    }]})
    out = a.parse_response(fr, ["X1"])
    _check("NO_NEXT_DATA produces 1 ItemStatus", len(out) == 1)
    _check("NO_NEXT_DATA item is in_stock=False",
           out[0].in_stock is False)
    _check("NO_NEXT_DATA preserves error string in availability_status",
           out[0].availability_status == "NO_NEXT_DATA")


def test_no_product_error():
    """JS template emits error='NO_PRODUCT' when __NEXT_DATA__ exists but
    has no product field (e.g., the URL redirected to a category page)."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "error": "NO_PRODUCT", "ms": 180, "http_status": 200,
    }]})
    out = a.parse_response(fr, ["X1"])
    _check("NO_PRODUCT produces 1 ItemStatus",
           len(out) == 1 and out[0].in_stock is False)
    _check("NO_PRODUCT preserves error string",
           out[0].availability_status == "NO_PRODUCT")


def test_missing_seller_fields_defensive_oos():
    """If a Walmart response has neither sellerId nor sellerName, we
    can't verify Walmart-direct, so we must default to OOS rather than
    risk buying from a third-party with mangled data."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Mystery Item",
            # NO sellerId, NO sellerName
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 50.00}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("missing seller fields → in_stock=False (defensive)",
           len(out) == 1 and out[0].in_stock is False,
           detail=f"got {out[0] if out else 'no result'}")


def test_preorder_sellable_in_stock():
    """PRE_ORDER_SELLABLE + showAtc=true + walmart_direct → in_stock=True.
    Pokemon drops sometimes use pre-order status; the bot must treat these
    as buyable."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Preorder Pokemon Set",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "PRE_ORDER_SELLABLE",
            "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 39.99}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("PRE_ORDER_SELLABLE + showAtc + direct → in_stock=True",
           len(out) == 1 and out[0].in_stock is True)
    _check("PRE_ORDER_SELLABLE preserved in availability_status",
           out[0].availability_status == "PRE_ORDER_SELLABLE")
    _check("PRE_ORDER price extracted",
           out[0].price == 39.99)


def test_preorder_marketplace_oos():
    """Pre-order from a third-party seller must STILL be in_stock=False —
    we only buy direct-from-Walmart even on pre-order."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Marketplace Preorder",
            "sellerId": "MARKETPLACE_FOO", "sellerName": "Scalper Store",
            "availabilityStatus": "PRE_ORDER_SELLABLE",
            "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 999.99}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("Marketplace PRE_ORDER → in_stock=False (Walmart-direct only)",
           len(out) == 1 and out[0].in_stock is False)


def test_marketplace_with_showAtc_true_oos():
    """Marketplace seller with showAtc=true MUST be in_stock=False.
    The IN_STOCK status + showAtc=True is meaningless if seller isn't
    Walmart-direct."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Marketplace IN_STOCK Item",
            "sellerId": "MARKETPLACE_XYZ", "sellerName": "Third Party",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 89.99}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("marketplace + IN_STOCK + showAtc → in_stock=False",
           len(out) == 1 and out[0].in_stock is False,
           detail=f"got {out[0] if out else None}")


def test_http_456_is_blocked():
    """HTTP 456 is Walmart's Akamai-block signature. Adapter's
    is_blocked_response must return True so ProxyState's park logic fires."""
    a = _adapter()
    fr = FetchResult(http_status=456, body_json={"results": [{
        "item_id": "X1", "error": "HTTP_456", "ms": 50, "http_status": 456,
    }]})
    _check("HTTP_456 → is_blocked_response True",
           a.is_blocked_response(fr) is True)


def test_http_403_is_blocked():
    """HTTP 403 = explicit forbidden. is_blocked_response True for park logic."""
    a = _adapter()
    fr = FetchResult(http_status=403, body_json={"results": [{
        "item_id": "X1", "error": "HTTP_403", "ms": 50, "http_status": 403,
    }]})
    _check("HTTP_403 → is_blocked_response True",
           a.is_blocked_response(fr) is True)


def test_aggregate_status_999_is_blocked():
    """JS aggregates worst per-item status as 999 if any item saw
    BLOCKED redirect. is_blocked_response must catch this."""
    a = _adapter()
    fr = FetchResult(http_status=999, body_json={"results": [
        {"item_id": "X1", "error": "BLOCKED", "ms": 50, "http_status": 200},
    ]})
    _check("aggregate 999 → is_blocked_response True",
           a.is_blocked_response(fr) is True)


def test_mixed_array_in_order():
    """Multiple items in one response — some OK, some errored, some queued.
    Parser must emit one ItemStatus per requested item, in REQUEST ORDER."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [
        {"item_id": "X1", "ms": 100, "product": {
            "usItemId": "X1", "name": "OK Item",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 10.0}},
        }},
        {"item_id": "X2", "error": "NO_NEXT_DATA", "ms": 200, "http_status": 200},
        {"item_id": "X3", "error": "QUEUED", "ms": 300, "http_status": 200,
         "queue_source": "body_signature", "queue_info": None},
    ]})
    out = a.parse_response(fr, ["X1", "X2", "X3"])
    _check("mixed array emits 3 ItemStatus", len(out) == 3)
    if len(out) == 3:
        _check("mixed[0] is in_stock IN_STOCK item",
               out[0].in_stock is True and out[0].item_id == "X1")
        _check("mixed[1] is NO_NEXT_DATA item",
               out[1].in_stock is False and out[1].item_id == "X2"
               and out[1].availability_status == "NO_NEXT_DATA")
        _check("mixed[2] is QUEUED → in_stock=True",
               out[2].in_stock is True and out[2].item_id == "X3"
               and "QUEUED" in (out[2].availability_status or ""))


def test_price_none_doesnt_crash():
    """priceInfo.currentPrice.price can be None on some catalog states.
    Parser must accept None price, not crash."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "No Price Item",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": None}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("price=None → still emits ItemStatus",
           len(out) == 1 and out[0].in_stock is True)
    _check("price=None preserved as None",
           out[0].price is None)


def test_missing_priceInfo_doesnt_crash():
    """priceInfo entirely missing — defensive parser."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Item with no priceInfo",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            # NO priceInfo at all
        }},
    ]})
    try:
        out = a.parse_response(fr, ["X1"])
        _check("missing priceInfo doesn't crash",
               len(out) == 1 and out[0].in_stock is True)
        _check("missing priceInfo → price=None",
               out[0].price is None)
    except Exception as e:
        _check("missing priceInfo doesn't crash", False,
               detail=f"raised {type(e).__name__}: {e}")


def test_seller_name_alone_works():
    """sellerId missing but sellerName='walmart.com' — adapter accepts the
    name-only check (per _extract_product line 297)."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Name-Only-Verified",
            "sellerId": "",   # empty (not None)
            "sellerName": "Walmart.com",   # name matches case-insensitively
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 19.99}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("sellerName='Walmart.com' alone → walmart_direct → in_stock=True",
           len(out) == 1 and out[0].in_stock is True,
           detail=f"got {out[0] if out else None}")


def test_seller_id_case_insensitive():
    """WALMART_SELLER_ID compared with .upper() — lowercase sellerId
    should still match."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "Lowercase sellerId",
            "sellerId": WALMART_SELLER_ID.lower(),   # lowercase variant
            "sellerName": "",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 25.0}},
        }},
    ]})
    out = a.parse_response(fr, ["X1"])
    _check("lowercase sellerId matches WALMART_SELLER_ID (case-insensitive)",
           len(out) == 1 and out[0].in_stock is True)


def test_usItemId_mismatch_returns_no_match():
    """If product.usItemId doesn't match requested item_id, _extract_product
    returns None → ItemStatus is emitted as PARSE_FAIL (defensive)."""
    a = _adapter()
    fr = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1",
        "ms": 200,
        "product": {
            "usItemId": "DIFFERENT_ID",   # mismatch
            "name": "Wrong product served",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 10.0}},
        },
    }]})
    out = a.parse_response(fr, ["X1"])
    _check("usItemId mismatch → in_stock=False (PARSE_FAIL or similar)",
           len(out) == 1 and out[0].in_stock is False,
           detail=f"got {out[0] if out else None}")


# ── runner ───────────────────────────────────────────────────────────────


TESTS = [
    ("body_json=None returns empty list", test_body_json_none_returns_empty_list),
    ("NO_NEXT_DATA error emits in_stock=False", test_no_next_data_error),
    ("NO_PRODUCT error emits in_stock=False", test_no_product_error),
    ("missing seller fields → defensive in_stock=False", test_missing_seller_fields_defensive_oos),
    ("PRE_ORDER_SELLABLE walmart-direct → in_stock=True", test_preorder_sellable_in_stock),
    ("PRE_ORDER marketplace → in_stock=False", test_preorder_marketplace_oos),
    ("marketplace + IN_STOCK + showAtc=true → in_stock=False", test_marketplace_with_showAtc_true_oos),
    ("HTTP_456 → is_blocked_response True", test_http_456_is_blocked),
    ("HTTP_403 → is_blocked_response True", test_http_403_is_blocked),
    ("aggregate status 999 → is_blocked_response True", test_aggregate_status_999_is_blocked),
    ("mixed array (in-stock + error + queued) in order", test_mixed_array_in_order),
    ("price=None doesn't crash", test_price_none_doesnt_crash),
    ("missing priceInfo doesn't crash", test_missing_priceInfo_doesnt_crash),
    ("sellerName='Walmart.com' alone → walmart_direct", test_seller_name_alone_works),
    ("lowercase sellerId matches (case-insensitive)", test_seller_id_case_insensitive),
    ("usItemId mismatch → in_stock=False", test_usItemId_mismatch_returns_no_match),
]


def main():
    print("=" * 70)
    print("Stock detection edge cases (Day 6)")
    print("=" * 70)
    for name, fn in TESTS:
        print(f"\n=== {name} ===")
        try:
            fn()
        except Exception as e:
            results.append(TestResult(
                name=name, status="FAIL",
                detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[:400]}",
            ))

    print()
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:3]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

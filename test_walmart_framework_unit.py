"""
Walmart framework unit-level integration test — NO NETWORK.

Exercises the framework + Walmart adapter against synthetic data to catch
Python-level bugs before Phase 1c spends manual logins / real BD bandwidth.

What this covers:
  - WalmartAdapter satisfies the Protocol (isinstance check)
  - build_fetch_js produces well-formed JS for various item counts
  - parse_response handles every documented response shape:
      * 200 with valid product → in_stock=True
      * 200 with OOS product → in_stock=False
      * 200 with third-party seller → in_stock=False (filtered out)
      * Error response (BLOCKED, HTTP_403, NO_NEXT_DATA, NO_PRODUCT) → no-stock + status
  - is_blocked_response correctly flags /blocked redirects and 403/456
  - is_preflight_clean correctly accepts/rejects responses
  - Dispatcher._interpret_eval_result handles all JS return shapes:
      * Normal {__http_status, __body_json, __body_text}
      * JS error {__err}
      * Unexpected (returns string, None, etc.)
  - ResilientChecker constructs cleanly with various sizings
  - Per-retailer state file path is correctly retailer-scoped

What this does NOT cover (Phase 1c does):
  - Real Chrome launch + tab.evaluate behavior
  - Real Walmart endpoint behavior (rate limits, response shapes in the wild)
  - Real proxy forwarder behavior under load

Run: python test_walmart_framework_unit.py
Exit code: 0 if all pass, 1 if any fail.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.stack.dispatcher import Dispatcher, DispatchResult
from src.stack.retailer_adapter import FetchResult, ItemStatus, RetailerAdapter
from walmart.walmart_adapter import WalmartAdapter, WALMART_SELLER_ID
from walmart.walmart_stock_resilient import build_walmart_checker


PASS = "PASS"
FAIL = "FAIL"


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, PASS if cond else FAIL, detail))


def _fail(name: str, exc: Exception):
    results.append(TestResult(
        name, FAIL,
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    ))


# ── Test 1: Protocol satisfaction ────────────────────────────────────────

def test_adapter_satisfies_protocol():
    adapter = WalmartAdapter()
    _check("adapter is RetailerAdapter", isinstance(adapter, RetailerAdapter))
    _check("adapter.name == walmart", adapter.name == "walmart")
    _check("adapter.needs_login", adapter.needs_login is True)
    _check("per_ip_rps_ceiling sane", 0 < adapter.per_ip_rps_ceiling <= 1.0,
           f"got {adapter.per_ip_rps_ceiling}")
    _check("chunk_size == 1", adapter.chunk_size == 1)
    _check("warmup_urls non-empty", len(adapter.warmup_urls) >= 3)
    _check("cookie_freshness_keys includes _px3",
           "_px3" in adapter.cookie_freshness_keys)


# ── Test 2: build_fetch_js shape ─────────────────────────────────────────

def test_build_fetch_js():
    adapter = WalmartAdapter()
    js = adapter.build_fetch_js(["320424995"])
    _check("js contains /ip/", "/ip/" in js)
    _check("js contains __NEXT_DATA__", "__NEXT_DATA__" in js)
    _check("js contains __http_status return key", "__http_status" in js)
    _check("js contains __body_json return key", "__body_json" in js)
    _check("js contains /blocked detection", "/blocked" in js)
    _check("js contains Promise.allSettled", "Promise.allSettled" in js)
    _check("js item-id substitution worked", "320424995" in js)

    # Multiple items
    js_multi = adapter.build_fetch_js(["100", "200", "300"])
    _check("js multi-item embeds all ids",
           all(i in js_multi for i in ["100", "200", "300"]))


# ── Test 3: parse_response — in stock ────────────────────────────────────

def test_parse_in_stock():
    adapter = WalmartAdapter()
    fr = FetchResult(http_status=200, body_json={"results": [
        {"item_id": "X1", "ms": 250, "product": {
            "usItemId": "X1", "name": "Test Product",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 19.99}},
        }},
    ]})
    out = adapter.parse_response(fr, ["X1"])
    _check("parse_in_stock returns 1 result", len(out) == 1)
    if out:
        _check("parse_in_stock in_stock=True", out[0].in_stock is True)
        _check("parse_in_stock title set", out[0].title == "Test Product")
        _check("parse_in_stock price set", out[0].price == 19.99)


# ── Test 4: parse_response — OOS ─────────────────────────────────────────

def test_parse_oos():
    adapter = WalmartAdapter()
    fr = FetchResult(http_status=200, body_json={"results": [
        {"item_id": "X2", "ms": 200, "product": {
            "usItemId": "X2", "name": "OOS Product",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "OUT_OF_STOCK", "showAtc": False,
            "priceInfo": {"currentPrice": {"price": 5.00}},
        }},
    ]})
    out = adapter.parse_response(fr, ["X2"])
    _check("parse_oos in_stock=False", len(out) == 1 and out[0].in_stock is False)


# ── Test 5: parse_response — third-party seller filtered ────────────────

def test_parse_third_party_filtered():
    adapter = WalmartAdapter()
    fr = FetchResult(http_status=200, body_json={"results": [
        {"item_id": "X3", "ms": 200, "product": {
            "usItemId": "X3", "name": "Third Party",
            "sellerId": "SOMEOTHERSELLER", "sellerName": "Marketplace Seller",
            "availabilityStatus": "IN_STOCK", "showAtc": True,
            "priceInfo": {"currentPrice": {"price": 999.99}},
        }},
    ]})
    out = adapter.parse_response(fr, ["X3"])
    _check("third-party in_stock=False even with IN_STOCK status",
           len(out) == 1 and out[0].in_stock is False)


# ── Test 6: parse_response — error pass-through ──────────────────────────

def test_parse_error_passthrough():
    adapter = WalmartAdapter()
    fr = FetchResult(http_status=200, body_json={"results": [
        {"item_id": "X4", "error": "NO_NEXT_DATA", "ms": 800},
        {"item_id": "X5", "error": "BLOCKED", "ms": 100, "http_status": 200},
    ]})
    out = adapter.parse_response(fr, ["X4", "X5"])
    _check("error items emit 2 ItemStatus", len(out) == 2)
    _check("error items all in_stock=False", all(not s.in_stock for s in out))
    _check("error items preserve error in availability_status",
           any(s.availability_status == "NO_NEXT_DATA" for s in out)
           and any(s.availability_status == "BLOCKED" for s in out))


# ── Test 7: is_blocked_response ──────────────────────────────────────────

def test_is_blocked():
    adapter = WalmartAdapter()
    # /blocked redirect aggregated status
    fr_blocked = FetchResult(http_status=999, body_json={"results": [
        {"item_id": "X", "error": "BLOCKED", "http_status": 200, "ms": 50}
    ]})
    _check("999 status flags blocked", adapter.is_blocked_response(fr_blocked))

    # HTTP_403 in per-item error
    fr_403 = FetchResult(http_status=403, body_json={"results": [
        {"item_id": "X", "error": "HTTP_403", "ms": 50}
    ]})
    _check("HTTP_403 in body flags blocked", adapter.is_blocked_response(fr_403))

    # Clean 200
    fr_clean = FetchResult(http_status=200, body_json={"results": [
        {"item_id": "X", "product": {"usItemId": "X"}, "ms": 200}
    ]})
    _check("clean 200 not blocked", not adapter.is_blocked_response(fr_clean))


# ── Test 8: is_preflight_clean ───────────────────────────────────────────

def test_preflight_clean():
    adapter = WalmartAdapter()
    _check("200 with normal body is clean",
           adapter.is_preflight_clean(200, "<html>walmart homepage</html>"))
    _check("403 is dirty", not adapter.is_preflight_clean(403, ""))
    _check("200 with 'robot or human' is dirty",
           not adapter.is_preflight_clean(
               200, "<html>Are you a robot or human?</html>"))
    _check("200 with 'px-captcha' is dirty",
           not adapter.is_preflight_clean(
               200, "<html>px-captcha</html>"))


# ── Test 9: Dispatcher._interpret_eval_result ────────────────────────────

class _FakeSession:
    """Stand-in for SessionEntry — only the attrs Dispatcher touches."""
    def __init__(self):
        self.id = "fake_s1"
        self.proxy_ip = "1.2.3.4"
        self.consecutive_errors = 0


def test_dispatcher_interpret_results():
    adapter = WalmartAdapter()

    # Need a Dispatcher instance; pool/items don't matter for this method
    class _NullPool:
        pass

    d = Dispatcher.__new__(Dispatcher)
    d.session_pool = _NullPool()
    d.adapter = adapter
    d.items = ["X"]
    d.tab_eval_timeout_s = 15.0
    d._chunks = [["X"]]
    d._next_chunk_idx = 0

    # 200 clean
    s = _FakeSession()
    raw = {"__http_status": 200, "__body_json": {"results": [
        {"item_id": "X", "product": {"usItemId": "X"}}
    ]}, "__body_text": None}
    r = d._interpret_eval_result(s, raw, 100.0)
    _check("200 clean — DispatchResult has fetch", r.fetch is not None)
    _check("200 clean — http_status=200", r.fetch.http_status == 200)
    _check("200 clean — consec_errors reset", s.consecutive_errors == 0)

    # 403 - non-200, should bump consec_errors
    s = _FakeSession()
    raw = {"__http_status": 403, "__body_json": None,
           "__body_text": "blocked"}
    r = d._interpret_eval_result(s, raw, 50.0)
    _check("403 — http_status=403", r.fetch.http_status == 403)
    _check("403 — consec_errors incremented", s.consecutive_errors == 1)

    # JS error
    s = _FakeSession()
    raw = {"__err": "TypeError: foo is not a function"}
    r = d._interpret_eval_result(s, raw, 75.0)
    _check("__err — http_status=0", r.fetch.http_status == 0)
    _check("__err — error string captured",
           r.fetch.error and "TypeError" in r.fetch.error)
    _check("__err — consec_errors incremented", s.consecutive_errors == 1)

    # Unexpected shape (e.g., JS returned a bare string)
    s = _FakeSession()
    r = d._interpret_eval_result(s, "unexpected", 25.0)
    _check("unexpected shape handled", r.fetch.http_status == 0)
    _check("unexpected shape — error captured",
           r.fetch.error and "unexpected_eval_result" in r.fetch.error)


# ── Test 10: ResilientChecker construction ───────────────────────────────

def test_checker_construction():
    def _cb(s: ItemStatus): pass
    c = build_walmart_checker(
        items=["X1"],
        on_in_stock=_cb,
        target_rps=1.0,
        num_chromes=2,
    )
    _check("checker.adapter.name == walmart", c.adapter.name == "walmart")
    _check("checker.target_aggregate_rps == 1.0", c.target_aggregate_rps == 1.0)
    _check("checker has 2 proxy_urls", len(c.proxy_urls) == 2)
    _check("checker.items == ['X1']", c.items == ["X1"])
    _check("retailer-scoped state path",
           str(c.proxy_state_path).endswith("walmart_proxy_state.json"))
    _check("retailer-scoped profile root",
           str(c.profile_root).endswith("walmart_session_profiles"))


# ── Queue detection (Pokemon-drop readiness) ─────────────────────────────

def test_fetch_js_includes_queue_detection():
    """The fetch JS must check for both /qp URL redirects AND body-embedded
    queue signatures so we never miss a queue interstitial.
    """
    adapter = WalmartAdapter()
    js = adapter.build_fetch_js(["X"])
    _check("js detects /qp in final_url", "'/qp'" in js or "/qp" in js)
    _check("js parses qpdata from URL", "qpdata=" in js)
    _check("js detects api.waiting-room body signature",
           "api.waiting-room.walmart.com" in js)
    _check("js detects issueTicket / checkTicket body signature",
           "issueTicket" in js and "checkTicket" in js)
    _check("js emits QUEUED error for caller", "QUEUED" in js)


def test_parse_queued_redirect_url_shape():
    """Queue redirect case: fetch JS reports the URL contained /qp+qpdata
    and parsed the JSON. Parser should emit in_stock=True with
    availability_status='QUEUED:redirect_url' and extract title/price
    from customMetadata.
    """
    adapter = WalmartAdapter()
    fake = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "999111",
        "error": "QUEUED",
        "ms": 350,
        "http_status": 200,
        "queue_source": "redirect_url",
        "queue_info": {
            "queued": True,
            "queue": "x21639376266",
            "url": "https://api.waiting-room.walmart.com/issueTicket?queue=x21639376266",
            "customMetadata": {
                "item": {
                    "itemID": "999111",
                    "name": "Pokemon TCG Test Box",
                    "currentPrice": "$49.99",
                },
            },
        },
    }]})
    out = adapter.parse_response(fake, ["999111"])
    _check("queue redirect → 1 ItemStatus", len(out) == 1)
    if out:
        s = out[0]
        _check("queue redirect → in_stock=True", s.in_stock is True)
        _check("queue redirect → availability='QUEUED:redirect_url'",
               s.availability_status == "QUEUED:redirect_url")
        _check("queue redirect → title extracted",
               s.title == "Pokemon TCG Test Box")
        _check("queue redirect → price parsed from '$49.99'",
               s.price == 49.99)


def test_parse_queued_body_signature_shape():
    """Queue body-signature case: fetch JS saw queue API names in the HTML
    body even though the URL didn't visibly redirect. queue_info may be
    None if the JS couldn't extract qpdata from the body.
    """
    adapter = WalmartAdapter()
    fake = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "999111",
        "error": "QUEUED",
        "ms": 280,
        "http_status": 200,
        "queue_source": "body_signature",
        "queue_info": None,
    }]})
    out = adapter.parse_response(fake, ["999111"])
    _check("queue body-sig → 1 ItemStatus", len(out) == 1)
    if out:
        s = out[0]
        _check("queue body-sig → in_stock=True (purchase flow fires)",
               s.in_stock is True)
        _check("queue body-sig → availability='QUEUED:body_signature'",
               s.availability_status == "QUEUED:body_signature")
        _check("queue body-sig → no title/price extracted (queue_info=None)",
               s.title is None and s.price is None)


def test_parse_queued_does_not_falsely_flag_oos():
    """A normal OOS response (no QUEUED error) must still emit in_stock=False
    so the queue detection doesn't accidentally fire on every cycle.
    """
    adapter = WalmartAdapter()
    fake = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "X1", "ms": 200, "product": {
            "usItemId": "X1", "name": "OOS Item",
            "sellerId": WALMART_SELLER_ID, "sellerName": "Walmart.com",
            "availabilityStatus": "OUT_OF_STOCK", "showAtc": False,
            "priceInfo": {"currentPrice": {"price": 9.99}},
        }},
    ]})
    out = adapter.parse_response(fake, ["X1"])
    _check("normal OOS doesn't trip queue path",
           len(out) == 1 and out[0].in_stock is False
           and not (out[0].availability_status or "").startswith("QUEUED"))


def test_parse_queued_with_malformed_price_doesnt_crash():
    """Queue payload with garbled currentPrice should not crash the parser —
    price stays None and the item is still emitted as in_stock=True.
    """
    adapter = WalmartAdapter()
    fake = FetchResult(http_status=200, body_json={"results": [{
        "item_id": "999111",
        "error": "QUEUED",
        "ms": 100,
        "http_status": 200,
        "queue_source": "redirect_url",
        "queue_info": {
            "customMetadata": {"item": {"name": "Bad Price", "currentPrice": "not-a-price"}},
        },
    }]})
    out = adapter.parse_response(fake, ["999111"])
    _check("garbled price → still emits ItemStatus",
           len(out) == 1 and out[0].in_stock is True)
    _check("garbled price → price=None", out and out[0].price is None)
    _check("garbled price → title still extracted",
           out and out[0].title == "Bad Price")


# ── in_queue flag — pool integration ─────────────────────────────────────


def test_pick_session_excludes_in_queue():
    """pick_session must skip sessions with in_queue=True so a queueing
    session isn't returned for a normal stock-check dispatch (which would
    consume its CPU and potentially the busy_lock, blocking queue progress).
    """
    from src.stack.multi_session_pool import MultiSessionPool, SessionEntry
    from pathlib import Path
    from unittest.mock import MagicMock

    pool = MultiSessionPool.__new__(MultiSessionPool)
    # Two synthetic sessions: s1 ready+in_queue=True, s2 ready+in_queue=False
    s1 = SessionEntry(
        id="s1", proxy_url="x", proxy_ip="1.1.1.1", local_port=25000,
        profile_dir=Path("/tmp/x1"), state="ready", cookies={"_px3": "abc"},
        in_queue=True, tab=MagicMock(),
    )
    s2 = SessionEntry(
        id="s2", proxy_url="y", proxy_ip="2.2.2.2", local_port=25001,
        profile_dir=Path("/tmp/x2"), state="ready", cookies={"_px3": "def"},
        in_queue=False, tab=MagicMock(),
    )
    pool.sessions = [s1, s2]
    # Pick 50 times — should ONLY ever return s2 since s1 is in queue
    picks = set()
    for _ in range(50):
        p = pool.pick_session()
        if p is not None:
            picks.add(p.id)
    _check("pick_session never picks in_queue=True session",
           "s1" not in picks)
    _check("pick_session does pick non-queueing sessions",
           "s2" in picks)


def test_session_entry_defaults_in_queue_false():
    """New sessions default to in_queue=False so existing dispatch behavior
    is unchanged for any caller that doesn't manage the flag.
    """
    from src.stack.multi_session_pool import SessionEntry
    from pathlib import Path
    s = SessionEntry(
        id="s1", proxy_url="x", proxy_ip="1.1.1.1", local_port=25000,
        profile_dir=Path("/tmp/x"),
    )
    _check("SessionEntry.in_queue defaults to False", s.in_queue is False)


# ── Test 11: Per-IP RPS ceiling validation (warn but don't crash) ────────

def test_checker_high_rps_warns():
    """If RPS would push per-IP over the adapter ceiling, the checker should
    log a warning but not crash. Documented behavior — caller's responsibility
    to obey the ceiling, framework just flags.
    """
    def _cb(s: ItemStatus): pass
    # 2 chromes * 5.0 rps = 2.5 RPS/IP, well over Walmart's 0.5 ceiling
    c = build_walmart_checker(
        items=["X1"],
        on_in_stock=_cb,
        target_rps=5.0,
        num_chromes=2,
    )
    _check("checker constructs even with high RPS (warning only)",
           c is not None)


# ── runner ───────────────────────────────────────────────────────────────

def main():
    tests = [
        ("Protocol satisfaction", test_adapter_satisfies_protocol),
        ("build_fetch_js shape", test_build_fetch_js),
        ("parse_response — in stock", test_parse_in_stock),
        ("parse_response — OOS", test_parse_oos),
        ("parse_response — third-party filtered", test_parse_third_party_filtered),
        ("parse_response — error pass-through", test_parse_error_passthrough),
        ("is_blocked_response", test_is_blocked),
        ("is_preflight_clean", test_preflight_clean),
        ("Dispatcher._interpret_eval_result", test_dispatcher_interpret_results),
        ("ResilientChecker construction", test_checker_construction),
        # Queue detection (Pokemon-drop readiness)
        ("Queue: fetch JS detects /qp + body signatures", test_fetch_js_includes_queue_detection),
        ("Queue: parse redirect-URL shape", test_parse_queued_redirect_url_shape),
        ("Queue: parse body-signature shape", test_parse_queued_body_signature_shape),
        ("Queue: normal OOS not falsely flagged", test_parse_queued_does_not_falsely_flag_oos),
        ("Queue: malformed price doesn't crash", test_parse_queued_with_malformed_price_doesnt_crash),
        # in_queue flag integration with pool
        ("in_queue: pick_session excludes queueing sessions",
         test_pick_session_excludes_in_queue),
        ("in_queue: SessionEntry defaults to False",
         test_session_entry_defaults_in_queue_false),
        ("High-RPS warns (no crash)", test_checker_high_rps_warns),
    ]
    print("=" * 70)
    print("Walmart framework unit tests (no network)")
    print("=" * 70)
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            _fail(name, e)

    print()
    fail_count = sum(1 for r in results if r.status == FAIL)
    pass_count = sum(1 for r in results if r.status == PASS)

    for r in results:
        marker = "✓" if r.status == PASS else "✗"
        print(f"  {marker} {r.name}")
        if r.status == FAIL and r.detail:
            for line in r.detail.split("\n")[:5]:
                print(f"      {line}")

    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

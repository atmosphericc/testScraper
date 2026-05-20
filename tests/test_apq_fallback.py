"""
APQ (Apollo Persisted Query) fallback — unit tests for the sentinel,
retry mechanics, and end-to-end hash rotation survival.

The critical correctness detail (Apollo Client #10253): the canonical
APQ-miss signal is `extensions.code == "PERSISTED_QUERY_NOT_FOUND"`,
NOT the error message text. Tests guard both the positive and negative
sides so the sentinel can't degrade into a too-narrow or too-broad match.

Six pure-Python tests + one end-to-end against the mitm sim.

Run: python tests/test_apq_fallback.py
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import urllib3   # noqa: E402
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from walmart.checkout_api import (   # noqa: E402
    apq_query_for, is_apq_miss, _load_apq_queries,
)
from walmart.walmart_adapter import WalmartAdapter   # noqa: E402


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


def _fail(name: str, exc: Exception):
    results.append(TestResult(
        name, "FAIL",
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:600]}",
    ))


# ── tests ────────────────────────────────────────────────────────────────


def test_sentinel_matches_extensions_code():
    """The canonical APQ-miss signal is extensions.code, not message text.
    Real Apollo Server can send arbitrary message text — we must match
    only on the code field per Apollo Client #10253."""

    # Positive: real-shape APQ miss
    body = {"errors": [{
        "message": "PersistedQueryNotFound",
        "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"},
    }]}
    _check("real-shape APQ miss → is_apq_miss True", is_apq_miss(body))

    # Positive: extensions.code only (message could be anything)
    body = {"errors": [{
        "message": "Some unrelated server error string here",
        "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"},
    }]}
    _check("APQ miss via extensions.code with unrelated message",
           is_apq_miss(body))

    # Negative: message-only is NOT a match (the critical correctness check)
    body = {"errors": [{
        "message": "PersistedQueryNotFound",
        "extensions": {"code": "OTHER_ERROR"},
    }]}
    _check("message='PersistedQueryNotFound' WITHOUT correct code → NOT a miss",
           not is_apq_miss(body))

    # Negative: missing extensions
    body = {"errors": [{"message": "PersistedQueryNotFound"}]}
    _check("missing extensions field → NOT a miss",
           not is_apq_miss(body))


def test_no_false_positive_on_other_error_codes():
    """Unrelated server errors must NOT trigger the APQ retry path —
    that would cause hash leakage and waste retry budgets."""
    cases = [
        {"errors": [{"extensions": {"code": "INTERNAL_SERVER_ERROR"}}]},
        {"errors": [{"extensions": {"code": "BAD_USER_INPUT"}}]},
        {"errors": [{"extensions": {"code": "UNAUTHENTICATED"}}]},
        {"errors": [{"extensions": {"code": "RATE_LIMITED"}}]},
        {"errors": [{}]},                                        # empty error
        {"errors": []},                                          # empty errors
        {},                                                       # no errors field
        {"data": {"foo": "ok"}},                                  # happy path
    ]
    for i, body in enumerate(cases):
        _check(f"case {i}: {str(body)[:60]} → no APQ miss",
               not is_apq_miss(body))


def test_sentinel_handles_malformed_inputs():
    """Defensive: malformed responses must not crash the detector."""
    cases = [None, "string", 42, [1, 2, 3], {"errors": "notalist"},
             {"errors": [None]}, {"errors": [{"extensions": "notadict"}]}]
    for body in cases:
        try:
            got = is_apq_miss(body)
            _check(f"malformed {type(body).__name__} → returns bool, not crash",
                   isinstance(got, bool))
        except Exception as e:
            _check(f"malformed {type(body).__name__} crashes", False,
                   detail=f"{type(e).__name__}: {e}")


def test_multiple_errors_one_apq():
    """If ANY error has the APQ sentinel, treat as APQ miss. Apollo Server
    can return multiple errors per response; we shouldn't miss APQ because
    a different error is listed first."""
    body = {"errors": [
        {"extensions": {"code": "OTHER_ERROR"}},
        {"extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"}},
    ]}
    _check("APQ miss as Nth error → still detected",
           is_apq_miss(body))


def test_all_required_query_bodies_present():
    """The five operations the bot uses must all have query bodies in
    walmart/checkout_apq_queries.json. Missing bodies = bot dies during
    next hash rotation."""
    required_ops = [
        "ItemByIdBtf",          # stock check (monitor)
        "updateItems",          # cart qty mutation
        "CreateContract",       # place order
        "getSlots",             # delivery slot fetch
        "reserveSlotMutation",  # slot booking
    ]
    for op in required_ops:
        q = apq_query_for(op)
        _check(f"{op}: query body present",
               q is not None and len(q) > 20,
               detail=f"got: {repr(q)[:60]}")


def test_unknown_op_returns_none():
    """apq_query_for(unknown op) returns None — caller must handle it
    gracefully (skip retry, log warning)."""
    _check("unknown op returns None",
           apq_query_for("DefinitelyNotARealOpName_xyz") is None)


def test_query_loader_caches():
    """_load_apq_queries() caches after first call — repeated lookups
    don't hit disk."""
    q1 = _load_apq_queries()
    q2 = _load_apq_queries()
    _check("query loader returns same object on repeated call",
           q1 is q2)


def test_adapter_apq_full_query_delegates():
    """WalmartAdapter.apq_full_query is the Protocol surface — must
    delegate to checkout_api.apq_query_for so framework + executor
    use the same source of truth."""
    a = WalmartAdapter()
    for op in ("ItemByIdBtf", "updateItems", "CreateContract"):
        q = a.apq_full_query(op)
        _check(f"adapter.apq_full_query({op}) returns query body",
               q is not None and len(q) > 20)
    _check("adapter.apq_full_query(unknown) returns None",
           a.apq_full_query("DefinitelyNotARealOpName_xyz") is None)


def test_query_bodies_are_valid_graphql_shape():
    """Light syntactic check: query bodies should start with 'query ' or
    'mutation ' and reference the operation name. Doesn't enforce strict
    GraphQL parsing (we don't ship a parser) but catches gross mistakes
    like copy-pasted wrong text."""
    queries = _load_apq_queries()
    for op_name, body in queries.items():
        starts_with_kw = body.startswith("query ") or body.startswith("mutation ")
        mentions_op = op_name in body
        _check(f"{op_name}: query body starts with 'query' or 'mutation'",
               starts_with_kw, detail=f"first 60: {body[:60]}")
        _check(f"{op_name}: query body mentions op name",
               mentions_op, detail=f"first 60: {body[:60]}")


# ── runner ───────────────────────────────────────────────────────────────


TESTS = [
    ("sentinel matches extensions.code (NOT message text)", test_sentinel_matches_extensions_code),
    ("no false positive on other error codes", test_no_false_positive_on_other_error_codes),
    ("sentinel handles malformed inputs without crash", test_sentinel_handles_malformed_inputs),
    ("multiple errors: one APQ → still detected", test_multiple_errors_one_apq),
    ("all 5 required ops have query bodies", test_all_required_query_bodies_present),
    ("unknown op returns None", test_unknown_op_returns_none),
    ("query loader caches", test_query_loader_caches),
    ("adapter.apq_full_query delegates correctly", test_adapter_apq_full_query_delegates),
    ("query bodies are valid GraphQL shape", test_query_bodies_are_valid_graphql_shape),
]


def main():
    print("=" * 70)
    print("APQ fallback — unit tests (Day 3)")
    print("=" * 70)
    for name, fn in TESTS:
        print(f"\n=== {name} ===")
        try:
            fn()
        except Exception as e:
            _fail(name, e)

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

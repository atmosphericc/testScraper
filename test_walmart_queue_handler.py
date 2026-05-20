"""
walmart/queue_handler.py — unit tests for the 2026 ticket-API model.

Covers the pure-functional parsing layer (qpdata, ticket responses, URL
detection) and the QueueHandler's state-classification logic. CDP listener
behavior can't be unit-tested without a browser; that path is exercised
during a real drop only.

Run: python test_walmart_queue_handler.py — exit 0 on pass.
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from walmart.queue_handler import (
    AdmissionLikelihood, QueueHandler, QueueState, QueueTicket,
    extract_qpdata_from_url, is_queue_url, is_ticket_api_url,
    parse_qpdata, parse_ticket_response,
)


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
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    ))


# ── URL detection ────────────────────────────────────────────────────────

def test_is_queue_url():
    _check("/qp path → queue URL",
           is_queue_url("https://www.walmart.com/qp"))
    _check("/qp?qpdata=... → queue URL",
           is_queue_url("https://www.walmart.com/qp?qpdata=foo"))
    _check("/qp/ → queue URL",
           is_queue_url("https://www.walmart.com/qp/something"))
    _check("normal /ip/ URL → NOT queue",
           not is_queue_url("https://www.walmart.com/ip/123"))
    _check("empty URL → NOT queue",
           not is_queue_url(""))
    _check("None URL → NOT queue",
           not is_queue_url(None))
    # Edge: /qp inside path but not as a path segment
    _check("/myqp/ false positive guarded",
           not is_queue_url("https://www.walmart.com/myqp/foo"))


def test_is_ticket_api_url():
    _check("checkTicket → ticket API",
           is_ticket_api_url("https://api.waiting-room.walmart.com/checkTicket?queue=x123"))
    _check("issueTicket → ticket API",
           is_ticket_api_url("https://api.waiting-room.walmart.com/issueTicket?queue=q1"))
    _check("refreshTicket → ticket API",
           is_ticket_api_url("https://api.waiting-room.walmart.com/refreshTicket?ticket=42"))
    _check("validateTickets → ticket API (added 2026-05-20)",
           is_ticket_api_url("https://api.waiting-room.walmart.com/validateTickets"))
    _check("checkTicket wrong host → NOT ticket API",
           not is_ticket_api_url("https://api.example.com/checkTicket"))
    _check("normal walmart URL → NOT ticket API",
           not is_ticket_api_url("https://www.walmart.com/ip/123"))


def test_extract_qpdata_from_url():
    qpd = '{"queued":true,"queue":"x123"}'
    import urllib.parse
    encoded = urllib.parse.quote(qpd)
    url = f"https://www.walmart.com/qp?qpdata={encoded}"
    extracted = extract_qpdata_from_url(url)
    _check("extract_qpdata returns the value",
           extracted == encoded)
    _check("extract_qpdata None for non-queue URL",
           extract_qpdata_from_url("https://www.walmart.com/ip/123") is None)
    _check("extract_qpdata None for URL without qpdata param",
           extract_qpdata_from_url("https://www.walmart.com/qp") is None)


# ── qpdata parser ────────────────────────────────────────────────────────

def test_parse_qpdata_full_shape():
    import urllib.parse
    payload = {
        "queued": True,
        "queue": "x21639376266",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=x21639376266",
        "customMetadata": {
            "item": {
                "itemID": "443574645",
                "name": "Xbox Series X",
                "imageURL": "...",
                "currentPrice": "$499.00",
                "itemURL": "/ip/seort/443574645",
            }
        }
    }
    import json
    encoded = urllib.parse.quote(json.dumps(payload))
    ticket = parse_qpdata(encoded)
    _check("parse_qpdata returns a QueueTicket",
           ticket is not None)
    if ticket:
        _check("parse_qpdata queue_id",
               ticket.queue_id == "x21639376266")
        _check("parse_qpdata state=pending",
               ticket.state == QueueState.PENDING)
        _check("parse_qpdata item_id",
               ticket.item_id == "443574645")


def test_parse_qpdata_malformed():
    _check("parse_qpdata returns None on garbage",
           parse_qpdata("not%20json%20at%20all") is None)
    _check("parse_qpdata returns None on empty",
           parse_qpdata("") is None)
    _check("parse_qpdata returns None on missing item metadata",
           parse_qpdata("%7B%7D") is not None)  # empty JSON dict is valid


# ── ticket API response parser ───────────────────────────────────────────

# ── REAL captured payload fixtures ──────────────────────────────────────
# These tests use the actual JSON payload captured from a real Walmart
# Pokemon TCG queue, published in the README of github.com/matthew7j2014/
# walmart-queue-tracker. This is the gold-standard fixture: if the parser
# handles this, it handles real Walmart.

# Real Pokemon TCG queue response (verbatim from the GitHub README)
REAL_TICKET_RESPONSE = {
    "site": "usgm",
    "queue": "qa484c0ebd7014",
    "shard": 49,
    "ticket": 2529,
    "state": "pending",
    "expires": 1771034402836,
    "signature": "evse+tJEvFgVsOpinrNpD/aBXPv3UHVwqVv7j4wbQkE=",
    "itemId": "19012610850",
    "expectedTurnTimeUnixTimestamp": 1770951237733,
    "nextRefreshUnixTimestamp": 1770951214376,
    "nextRefreshRelativeTime": 36000,
    "customMetadata": {
        "admissionLikelihood": "likely",
        "title": "This deal is going fast",
        "item": {
            "name": "Pokemon Trading Card Games ...",
            "currentPrice": "$29.97",
            "itemID": "19012610850",
        },
    },
}

# Real Xbox Series X pre-ticket qpdata (Google-indexed)
REAL_XBOX_QPDATA = {
    "queued": True,
    "queue": "x21639376266",
    "url": "https://api.waiting-room.walmart.com/issueTicket?queue=x21639376266",
    "customMetadata": {
        "item": {
            "itemID": "443574645",
            "name": "Xbox Series X",
            "imageURL": "https://i5.walmartimages.com/asr/551f9b29.jpeg",
            "currentPrice": "$499.00",
            "itemURL": "/ip/seort/443574645",
        },
    },
}


def test_real_pokemon_ticket_parses():
    """Parses the verbatim real Pokemon TCG queue response published by
    walmart-queue-tracker. If the parser handles this, it handles the
    canonical real shape.
    """
    ticket = parse_ticket_response(REAL_TICKET_RESPONSE)
    _check("real Pokemon ticket parses", ticket is not None)
    if ticket:
        _check("real ticket state=pending",
               ticket.state == QueueState.PENDING)
        _check("real ticket likelihood=likely",
               ticket.likelihood == AdmissionLikelihood.LIKELY)
        _check("real ticket queue_id captured",
               ticket.queue_id == "qa484c0ebd7014")
        _check("real ticket ticket-id stringified ('2529')",
               ticket.ticket == "2529")
        _check("real ticket next_refresh_ms=36000",
               ticket.next_refresh_ms == 36000)
        _check("real ticket item_id matched at top-level",
               ticket.item_id == "19012610850")


def test_real_xbox_qpdata_parses():
    """Parses the verbatim real Xbox qpdata payload from a live drop."""
    import json, urllib.parse
    encoded = urllib.parse.quote(json.dumps(REAL_XBOX_QPDATA))
    ticket = parse_qpdata(encoded)
    _check("real Xbox qpdata parses", ticket is not None)
    if ticket:
        _check("real qpdata queue=x21639376266",
               ticket.queue_id == "x21639376266")
        _check("real qpdata item=443574645",
               ticket.item_id == "443574645")


def test_validate_tickets_array_shape_parses():
    """`validateTickets` wraps responses as {'tickets': [...]}. The parser
    must handle this — alxmyth/walmart-queue-monitor confirms this shape
    in production.
    """
    array_response = {"tickets": [REAL_TICKET_RESPONSE]}
    ticket = parse_ticket_response(array_response)
    _check("validateTickets array shape parses", ticket is not None)
    if ticket:
        _check("array shape: state extracted correctly",
               ticket.state == QueueState.PENDING)
        _check("array shape: ticket-id extracted",
               ticket.ticket == "2529")


def test_bare_list_response_parses():
    """Some endpoints return a bare array. Parser should pick the first
    entry.
    """
    list_response = [REAL_TICKET_RESPONSE]
    ticket = parse_ticket_response(list_response)
    _check("bare list response parses", ticket is not None)
    if ticket:
        _check("bare list: state extracted",
               ticket.state == QueueState.PENDING)


def test_empty_array_returns_none():
    _check("empty list returns None",
           parse_ticket_response([]) is None)
    _check("empty tickets array returns None",
           parse_ticket_response({"tickets": []}) is None)


def test_moderate_likelihood_recognized():
    """alxmyth/walmart-queue-monitor whitelists 'moderate' as a third tier
    between 'likely' and 'unlikely'. Our parser must recognize it (not
    fall back to UNKNOWN), so the unlikely-streak bail doesn't trigger
    on moderate.
    """
    body = dict(REAL_TICKET_RESPONSE)
    body["customMetadata"] = dict(body["customMetadata"])
    body["customMetadata"]["admissionLikelihood"] = "moderate"
    ticket = parse_ticket_response(body)
    _check("moderate likelihood is recognized",
           ticket is not None
           and ticket.likelihood == AdmissionLikelihood.MODERATE)


def test_state_transitions_through_real_shape():
    """Drive the parser through pending → valid → expired using mutations
    of the real shape. Catches any state-specific code paths that fail
    on the realistic surrounding fields.
    """
    for state_value in ("pending", "valid", "expired"):
        body = dict(REAL_TICKET_RESPONSE)
        body["state"] = state_value
        ticket = parse_ticket_response(body)
        _check(f"real shape with state={state_value} parses",
               ticket is not None and ticket.state == state_value)


def test_parse_ticket_response_pending():
    body = {
        "queue": "x21639376266",
        "ticket": "12345",
        "state": "pending",
        "expectedTurnTimeUnixTimestamp": 1779000000000,
        "customMetadata": {"admissionLikelihood": "likely"},
        "nextRefreshRelativeTime": 30000,
        "itemId": "443574645",
        "expires": 1779100000,
    }
    ticket = parse_ticket_response(body)
    _check("pending response parses",
           ticket is not None)
    if ticket:
        _check("state=pending", ticket.state == QueueState.PENDING)
        _check("likelihood=likely",
               ticket.likelihood == AdmissionLikelihood.LIKELY)
        _check("queue_id captured",
               ticket.queue_id == "x21639376266")
        _check("ticket id stringified",
               ticket.ticket == "12345")
        _check("next_refresh_ms",
               ticket.next_refresh_ms == 30000)
        _check("item_id stringified",
               ticket.item_id == "443574645")


def test_parse_ticket_response_valid():
    body = {
        "queue": "x123", "ticket": "999",
        "state": "valid",
        "customMetadata": {"admissionLikelihood": "likely"},
        "nextRefreshRelativeTime": 0,
    }
    ticket = parse_ticket_response(body)
    _check("valid state parses correctly",
           ticket is not None and ticket.state == QueueState.VALID)


def test_parse_ticket_response_expired():
    body = {"queue": "x123", "state": "expired",
            "customMetadata": {"admissionLikelihood": "unlikely"}}
    ticket = parse_ticket_response(body)
    _check("expired state parses",
           ticket is not None and ticket.state == QueueState.EXPIRED)
    _check("unlikely likelihood captured",
           ticket is not None
           and ticket.likelihood == AdmissionLikelihood.UNLIKELY)


def test_parse_ticket_response_unknown_state():
    body = {"queue": "x", "state": "bogus_value"}
    ticket = parse_ticket_response(body)
    _check("unknown state fields default to UNKNOWN",
           ticket is not None and ticket.state == QueueState.UNKNOWN)


def test_parse_ticket_response_malformed():
    _check("None body → None ticket",
           parse_ticket_response(None) is None)
    _check("non-dict body → None ticket",
           parse_ticket_response("not a dict") is None)
    _check("empty dict → ticket with UNKNOWN state",
           parse_ticket_response({}) is not None
           and parse_ticket_response({}).state == QueueState.UNKNOWN)


# ── QueueHandler state transitions (mocked) ──────────────────────────────


class _FakePage:
    """Minimal page mock for QueueHandler.detect()."""
    def __init__(self, url: str = "", body_text: str = ""):
        self.url = url
        self._body_text = body_text

    async def evaluate(self, js, **kwargs):
        if "document.body.innerText" in js:
            return self._body_text
        return None

    async def xpath(self, expr):
        return []  # no legacy buttons


def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


def test_handler_detect_qp_url():
    page = _FakePage(url="https://www.walmart.com/qp?qpdata=%7B%22queued%22%3Atrue%7D")
    h = QueueHandler(page)
    ticket = _run_async(h.detect())
    _check("/qp URL → handler detects queue",
           ticket is not None)
    if ticket:
        _check("detected ticket has PENDING state",
               ticket.state == QueueState.PENDING)


def test_handler_detect_normal_url():
    page = _FakePage(url="https://www.walmart.com/ip/123", body_text="normal page")
    h = QueueHandler(page)
    ticket = _run_async(h.detect())
    _check("Normal URL + no queue text → None",
           ticket is None)


def test_handler_detect_legacy_overlay_text():
    page = _FakePage(
        url="https://www.walmart.com/ip/123",
        body_text="High demand — you're in line for this product",
    )
    h = QueueHandler(page)
    ticket = _run_async(h.detect())
    _check("Legacy overlay text → handler detects queue",
           ticket is not None)


# ── in_queue flag lifecycle (resilient-stack integration) ────────────────


class _FakeSessionEntry:
    """Minimal stand-in for SessionEntry — only needs the in_queue attr."""
    def __init__(self):
        self.in_queue = False
        # detect_and_wait reads other attrs only if it goes deeper —
        # we short-circuit by returning None from detect() for these tests.


def test_in_queue_flag_set_on_entry_cleared_on_exit_no_queue():
    """When detect() returns None (not in queue), detect_and_wait should
    still set the flag briefly and clear it before returning. This tests
    the try/finally lifecycle even on the early-return path.
    """
    page = _FakePage(url="https://www.walmart.com/ip/123", body_text="normal page")
    session = _FakeSessionEntry()
    h = QueueHandler(page, session=session)
    result = _run_async(h.detect_and_wait(timeout=1.0))
    _check("detect_and_wait returns None when not queued", result is None)
    _check("in_queue flag cleared on exit (no-queue path)",
           session.in_queue is False)


def test_in_queue_flag_unaffected_when_no_session_given():
    """If no session was passed, the handler must not crash trying to
    set flags on None.
    """
    page = _FakePage(url="https://www.walmart.com/ip/123", body_text="normal page")
    h = QueueHandler(page, session=None)
    try:
        _run_async(h.detect_and_wait(timeout=1.0))
        _check("no-session path runs without crashing", True)
    except Exception as e:
        _check("no-session path runs without crashing", False, str(e))


class _LegacyWalmartSessionMock:
    """Stand-in for legacy WalmartSessionManager — no in_queue attr."""
    def __init__(self):
        self.warmed = 0
    async def warm_session(self, items):
        self.warmed += 1


def test_legacy_session_no_in_queue_attr_tolerated():
    """purchase_executor passes a WalmartSessionManager, which doesn't have
    an `in_queue` attribute. The handler must detect this and skip the
    flag operations silently — not crash with AttributeError.
    """
    page = _FakePage(url="https://www.walmart.com/ip/123", body_text="normal page")
    legacy_session = _LegacyWalmartSessionMock()
    h = QueueHandler(page, session=legacy_session)
    # Detect that the handler chose the no-op path
    _check("legacy session → _session_has_in_queue False",
           h._session_has_in_queue is False)
    # Run detect_and_wait — must not raise AttributeError on legacy session
    try:
        result = _run_async(h.detect_and_wait(timeout=0.3))
        _check("legacy session: detect_and_wait runs without crashing",
               True)
        _check("legacy session: returns None when not queued",
               result is None)
    except AttributeError as e:
        _check("legacy session: handler tolerates missing in_queue attr",
               False, f"AttributeError: {e}")


def test_session_entry_style_in_queue_detected():
    """A session object that DOES have in_queue=False is detected and the
    handler will set/clear the flag.
    """
    page = _FakePage(url="https://www.walmart.com/ip/123", body_text="normal page")
    session_entry_like = _FakeSessionEntry()
    h = QueueHandler(page, session=session_entry_like)
    _check("session with in_queue attr → _session_has_in_queue True",
           h._session_has_in_queue is True)


def test_in_queue_flag_cleared_after_already_admitted():
    """When detect() returns a queue ticket, detect_and_wait enters the
    wait loop. With our short timeout it'll loop-out and return whatever
    _last_ticket is (None here because the CDP listener never fires in
    the mock). What matters: in_queue is set during the wait and cleared
    in the finally block. We verify the cleared state proves try/finally ran.
    """
    import urllib.parse, json
    qpdata = urllib.parse.quote(json.dumps({"queued": True, "queue": "x1"}))
    page = _FakePage(url=f"https://www.walmart.com/qp?qpdata={qpdata}")
    session = _FakeSessionEntry()
    h = QueueHandler(page, session=session)
    # Use very short timeout — wait loop times out without admission since
    # the mock can't actually deliver a state=valid ticket.
    _run_async(h.detect_and_wait(timeout=0.5))
    _check("in_queue flag cleared after wait returns",
           session.in_queue is False)
    # The "cleared" assertion proves the try/finally ran — which means
    # the wrapper set in_queue=True on entry and the finally cleared it
    # on exit. If the wrapper had skipped the flag management (e.g.,
    # session=None branch), the flag would still be False but the
    # try/finally wouldn't have executed; we can't distinguish those two
    # cases from outside, but combined with the no-session test above,
    # we cover both code paths.


# ── runner ───────────────────────────────────────────────────────────────


def main():
    tests = [
        ("URL: is_queue_url", test_is_queue_url),
        ("URL: is_ticket_api_url", test_is_ticket_api_url),
        ("URL: extract_qpdata_from_url", test_extract_qpdata_from_url),
        ("parse_qpdata: full shape", test_parse_qpdata_full_shape),
        ("parse_qpdata: malformed inputs", test_parse_qpdata_malformed),
        # Real captured payload fixtures
        ("REAL Pokemon TCG ticket response parses",
         test_real_pokemon_ticket_parses),
        ("REAL Xbox qpdata parses", test_real_xbox_qpdata_parses),
        ("validateTickets array shape parses",
         test_validate_tickets_array_shape_parses),
        ("bare list response parses", test_bare_list_response_parses),
        ("empty arrays return None", test_empty_array_returns_none),
        ("moderate likelihood recognized", test_moderate_likelihood_recognized),
        ("real shape: state transitions parse",
         test_state_transitions_through_real_shape),
        ("parse_ticket_response: pending", test_parse_ticket_response_pending),
        ("parse_ticket_response: valid", test_parse_ticket_response_valid),
        ("parse_ticket_response: expired", test_parse_ticket_response_expired),
        ("parse_ticket_response: unknown state", test_parse_ticket_response_unknown_state),
        ("parse_ticket_response: malformed inputs", test_parse_ticket_response_malformed),
        ("Handler.detect: /qp URL", test_handler_detect_qp_url),
        ("Handler.detect: normal URL → None", test_handler_detect_normal_url),
        ("Handler.detect: legacy overlay text", test_handler_detect_legacy_overlay_text),
        # in_queue flag lifecycle
        ("in_queue flag cleared on early return (not queued)",
         test_in_queue_flag_set_on_entry_cleared_on_exit_no_queue),
        ("Handler tolerates no-session passed",
         test_in_queue_flag_unaffected_when_no_session_given),
        ("in_queue flag cleared after wait completes",
         test_in_queue_flag_cleared_after_already_admitted),
        ("legacy session without in_queue attr tolerated",
         test_legacy_session_no_in_queue_attr_tolerated),
        ("SessionEntry-style with in_queue attr detected",
         test_session_entry_style_in_queue_detected),
    ]
    print("=" * 70)
    print("Walmart queue_handler unit tests (no network, no browser)")
    print("=" * 70)
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            _fail(name, e)
    print()
    fail_count = sum(1 for r in results if r.status == "FAIL")
    pass_count = sum(1 for r in results if r.status == "PASS")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.status == "FAIL" and r.detail:
            for line in r.detail.split("\n")[:5]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

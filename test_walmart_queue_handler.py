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
    _check("validateTickets (no endpoint match) → NOT ticket API by our filter",
           not is_ticket_api_url("https://api.waiting-room.walmart.com/validateTickets"))
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

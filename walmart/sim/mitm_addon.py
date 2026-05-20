"""
mitmproxy addon — routes Walmart-shaped requests to scripted responses.

Load via:
  mitmdump -s walmart/sim/mitm_addon.py --listen-port 8089

The addon installs itself via the module-level `addons` list mitmproxy looks
for. State is held in a module-level SimState instance accessible by tests
that want to script behavior (via `from walmart.sim.mitm_addon import SIM_STATE`).

For programmatic use (preferred from tests), use walmart/sim/server.py which
spawns mitmdump in-process via DumpMaster and exposes a SimState handle.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from mitmproxy import http

from walmart.sim.state import (
    AdmissionLikelihood,
    ItemAvailability,
    QueueState,
    QueueScenario,
    SimState,
    build_qpdata_url,
    build_ticket_response,
)


logger = logging.getLogger("walmart.sim.addon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIM] %(message)s")


# Singleton state — tests grab a reference via `from walmart.sim.mitm_addon import SIM_STATE`.
# The server.py wrapper replaces this with its own instance when starting
# programmatically, so we don't end up with two competing state objects.
SIM_STATE: SimState = SimState()


def set_sim_state(state: SimState) -> None:
    """Replace the addon's singleton state (used by server.py harness)."""
    global SIM_STATE
    SIM_STATE = state


# ── URL pattern matchers ─────────────────────────────────────────────────

_RE_IP = re.compile(r"^/ip/([\w-]+)/?$")
_RE_TICKET_API = re.compile(
    r"^https?://api\.waiting-room\.walmart\.com/(issueTicket|checkTicket|refreshTicket|validateTickets)"
)
_RE_GRAPHQL_OP = re.compile(
    # Hash is normally hex SHA-256 but we accept any non-slash sequence so
    # placeholder tests + future format changes still hit the sim, not
    # passthrough.
    r"^/orchestra/[\w-]+/graphql/(\w+)/([^/?#]+)/?$"
)
_RE_GETKEY = re.compile(
    r"^https?://securedataweb\.walmart\.com/pie/v\d+/[\w_]+/getkey\.js"
)


def _json_response(body: dict[str, Any], status: int = 200) -> http.Response:
    return http.Response.make(
        status,
        json.dumps(body).encode("utf-8"),
        {"Content-Type": "application/json"},
    )


def _html_response(body: bytes | str, status: int = 200) -> http.Response:
    if isinstance(body, str):
        body = body.encode("utf-8")
    return http.Response.make(
        status, body, {"Content-Type": "text/html; charset=utf-8"},
    )


def _redirect_response(location: str, status: int = 302) -> http.Response:
    return http.Response.make(
        status, b"", {"Location": location, "Content-Type": "text/html"},
    )


# ── route handlers ──────────────────────────────────────────────────────


def _route_ip_page(flow: http.HTTPFlow, item_id: str) -> http.Response:
    availability = SIM_STATE.get_item(item_id)
    SIM_STATE.log_request(route="/ip/", item_id=item_id, availability=availability.value)

    if availability == ItemAvailability.QUEUED:
        # 302 → /qp?qpdata=<encoded JSON>
        # Pick the first configured queue or build a default one keyed by item
        queue = next(iter(SIM_STATE.queues.values()), None)
        if queue is None:
            queue = QueueScenario(item_id=item_id)
            SIM_STATE.set_queue(queue)
        qpdata = build_qpdata_url(queue.queue_id, item_id)
        return _redirect_response(f"/qp?qpdata={qpdata}")

    if availability == ItemAvailability.IN_STOCK:
        return _html_response(SimState.load_fixture("ip_in_stock.html"))
    if availability == ItemAvailability.THIRD_PARTY:
        return _html_response(SimState.load_fixture("ip_third_party.html"))
    # default: OOS
    return _html_response(SimState.load_fixture("ip_oos.html"))


def _route_qp(flow: http.HTTPFlow) -> http.Response:
    SIM_STATE.log_request(route="/qp", url=flow.request.url)
    return _html_response(SimState.load_fixture("qp_pending.html"))


def _route_ticket_api(flow: http.HTTPFlow) -> http.Response:
    parsed = urlparse(flow.request.url)
    params = parse_qs(parsed.query)
    queue_id = (params.get("queue") or [None])[0]
    endpoint = parsed.path.lstrip("/")

    if queue_id is None:
        return _json_response({"error": "missing queue param"}, status=400)

    scenario = SIM_STATE.advance_queue(queue_id)
    if scenario is None:
        # Auto-create with defaults so tests that don't pre-configure still work
        scenario = QueueScenario(queue_id=queue_id)
        SIM_STATE.set_queue(scenario)
        scenario = SIM_STATE.advance_queue(queue_id)

    body = build_ticket_response(scenario)
    SIM_STATE.log_request(
        route="ticket_api", endpoint=endpoint, queue_id=queue_id,
        poll_count=scenario.poll_count, state=body["state"],
        likelihood=body["customMetadata"]["admissionLikelihood"],
    )

    if endpoint == "validateTickets":
        # Wrap in {"tickets": [...]} shape (per alxmyth/walmart-queue-monitor)
        return _json_response({"tickets": [body]})
    return _json_response(body)


def _route_graphql(flow: http.HTTPFlow, op_name: str, hash_value: str) -> http.Response:
    # Did the client include a full-query body (APQ fallback)?
    has_full_query = False
    body_json: dict[str, Any] = {}
    if flow.request.method == "POST":
        try:
            body_json = json.loads(flow.request.text or "{}")
        except (json.JSONDecodeError, TypeError):
            body_json = {}
        # Apollo full-query body has top-level `query` field
        has_full_query = "query" in body_json and bool(body_json.get("query"))

    SIM_STATE.record_graphql_request(
        op_name, hash_value, has_full_query, body_json,
    )

    if not SIM_STATE.is_hash_known(op_name, hash_value) and not has_full_query:
        # Force APQ miss — client should retry with full `query` body
        return _json_response({
            "errors": [{
                "message": "PersistedQueryNotFound",
                "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"},
            }],
        })

    # Hash is known OR full query was provided — return canned success
    # Operation-specific success body
    return _graphql_success_body(op_name, hash_value)


def _graphql_success_body(op_name: str, hash_value: str) -> http.Response:
    """Return canned success for a GraphQL op. Day 3 + Day 4 will extend
    these as APQ + hybrid-checkout tests come online."""
    canned = {
        "updateItems": {
            "data": {
                "updateItems": {
                    "lineItems": [{"quantity": 5}],
                    "checkoutable": True,
                },
            },
            "extensions": {"persistedQuery": {"sha256Hash": hash_value, "version": 1}},
        },
        "CreateContract": {
            "data": {
                "createPurchaseContract": {
                    "id": "test-pcid-12345",
                    "order": {"status": "PLACED"},
                    "payments": [{"amountPaid": "29.97", "lastFour": "1234"}],
                },
            },
            "extensions": {"persistedQuery": {"sha256Hash": hash_value, "version": 1}},
        },
        "getSlots": {
            "data": {
                "slots": {
                    "slotDays": [{
                        "eachDaySlots": [{
                            "id": "slot-1", "available": True, "isSelectable": True,
                            "fulfillmentType": "SCHEDULED_DELIVERY",
                            "price": {"total": {"value": 0.0}},
                            "startTime": "2026-05-21T10:00:00Z",
                            "slaInMins": 60, "slotMetadata": "sm1",
                        }],
                    }],
                },
            },
        },
        "reserveSlotMutation": {
            "data": {"reserveSlot": {"checkoutable": True}},
        },
        "ItemByIdBtf": {
            "data": {"product": {"name": "Test Item", "availabilityStatus": "IN_STOCK"}},
        },
    }
    body = canned.get(op_name, {"data": {}})
    return _json_response(body)


def _route_getkey(flow: http.HTTPFlow) -> http.Response:
    """PIE.js public-key endpoint. Returns the JS body wrapped as a script."""
    SIM_STATE.log_request(route="getkey.js", url=flow.request.url)
    if SIM_STATE.pie_pubkey_response is not None:
        # Render canned pubkey response as a JS object literal
        body = "var PIE = " + json.dumps(SIM_STATE.pie_pubkey_response) + ";"
        return http.Response.make(
            200, body.encode("utf-8"),
            {"Content-Type": "application/javascript"},
        )
    # Fallback: serve the static fixture (added Day 4)
    try:
        body_bytes = SimState.load_fixture("getkey.js")
        return http.Response.make(
            200, body_bytes, {"Content-Type": "application/javascript"},
        )
    except FileNotFoundError:
        # Day 1: getkey fixture may not exist yet — return a minimal one
        body = (
            'var PIE = {"K0c":"test-keyid","key_id":"test-keyid",'
            '"phase":"1","L":3072,"n":"abcd","e":"AQAB"};'
        )
        return http.Response.make(
            200, body.encode("utf-8"),
            {"Content-Type": "application/javascript"},
        )


def _route_pie_submit(flow: http.HTTPFlow) -> http.Response:
    """Accept the PIE-encrypted CVV payload. Day 4 will decrypt + assert."""
    try:
        body_json = json.loads(flow.request.text or "{}")
    except (json.JSONDecodeError, TypeError):
        body_json = {}
    SIM_STATE.log_request(
        route="pie_submit", path=flow.request.path,
        has_encryptedCvv="encryptedCvv" in body_json,
    )
    return _json_response({"cardToken": "test-card-token", "status": "ok"})


# ── mitmproxy hook ───────────────────────────────────────────────────────


def request(flow: http.HTTPFlow) -> None:
    """Single request hook. Dispatches by URL pattern."""
    url = flow.request.url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"

    # Ticket API (full URL match because it's a different host)
    if _RE_TICKET_API.match(url):
        flow.response = _route_ticket_api(flow)
        return

    # PIE getkey.js
    if _RE_GETKEY.match(url):
        flow.response = _route_getkey(flow)
        return

    # PIE submission (path-based, can be on multiple Walmart hosts)
    if path.startswith("/api/checkout-customer/"):
        flow.response = _route_pie_submit(flow)
        return

    # walmart.com routes (host check excludes external)
    if "walmart.com" in host:
        if path.startswith("/qp"):
            flow.response = _route_qp(flow)
            return

        # GraphQL operations: /orchestra/<service>/graphql/<op>/<hash>
        gql_match = _RE_GRAPHQL_OP.match(path)
        if gql_match:
            op_name, hash_value = gql_match.group(1), gql_match.group(2)
            flow.response = _route_graphql(flow, op_name, hash_value)
            return

        ip_match = _RE_IP.match(path)
        if ip_match:
            flow.response = _route_ip_page(flow, ip_match.group(1))
            return

    # Anything else — pass through (real network). For tests we run with
    # only-walmart upstreams, so this should be rare.
    SIM_STATE.log_request(route="passthrough", url=url[:120])


# mitmproxy looks for this module-level attribute
addons = []   # we use the bare `request` hook instead of an addon class

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
import time
from typing import Any, Optional
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


def _auth_cookie_header() -> dict[str, str]:
    """Set the `auth` cookie that walmart/session_manager.py:validate_session
    checks for. Real Walmart sets this on login; sim sets it on every response
    so the bot's "am I logged in" check always passes against the sim."""
    return {
        "Set-Cookie": "auth=sim-test-auth-cookie; Path=/; Domain=.walmart.com; SameSite=Lax",
    }


def _cors_headers(flow: http.HTTPFlow) -> dict[str, str]:
    """CORS headers permissive enough for any cross-origin fetch from
    walmart.com → api.waiting-room.walmart.com. Chrome enforces these
    when the queue page's JS uses `credentials: "include"`.

    Real Walmart's CORS config presumably has specific allow-origin rules;
    we mirror with `*` for the origin EXCEPT when credentials are required,
    in which case we must echo the request's Origin header (Access-Control-
    Allow-Origin: * is forbidden with credentials).

    Also: Cache-Control: no-store on every sim response so Chrome can't
    serve subsequent polls from its HTTP cache. Without this, repeated
    `fetch(checkTicket_url)` calls hit cache after the first, and the
    Network.responseReceived event fires only once per unique URL.
    """
    origin = flow.request.headers.get("Origin", "")
    base = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    if origin:
        base.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers":
                "Content-Type, X-Requested-With, X-APOLLO-OPERATION-NAME, "
                "X-O-Bu, X-O-Mart, X-O-Platform, X-O-Segment, X-O-Ccm, "
                "X-O-Gql-Query, X-O-Platform-Version, X-Enable-Server-Timing",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT",
            "Vary": "Origin",
        })
    else:
        base.update({
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT",
            "Access-Control-Allow-Headers":
                "Content-Type, X-Requested-With, X-APOLLO-OPERATION-NAME",
        })
    return base


def _json_response(body: dict[str, Any], status: int = 200,
                   flow: Optional[http.HTTPFlow] = None) -> http.Response:
    headers = {"Content-Type": "application/json"}
    if flow is not None:
        headers.update(_cors_headers(flow))
    return http.Response.make(
        status, json.dumps(body).encode("utf-8"), headers,
    )


def _html_response(body: bytes | str, status: int = 200,
                   flow: Optional[http.HTTPFlow] = None) -> http.Response:
    if isinstance(body, str):
        body = body.encode("utf-8")
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        # Force Chrome to fetch fresh on every call. Without this, the
        # browser caches /ip/<sku> and re-uses the stale (often OOS)
        # response when state changes mid-run.
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    if flow is not None:
        headers.update(_cors_headers(flow))
        # Set walmart.com auth cookie so bot's login check passes against sim
        if flow.request.host and "walmart.com" in flow.request.host:
            headers.update(_auth_cookie_header())
    return http.Response.make(status, body, headers)


def _redirect_response(location: str, status: int = 302,
                       flow: Optional[http.HTTPFlow] = None) -> http.Response:
    headers = {"Location": location, "Content-Type": "text/html"}
    if flow is not None:
        headers.update(_cors_headers(flow))
    return http.Response.make(status, b"", headers)


# ── route handlers ──────────────────────────────────────────────────────


def _route_ip_page(flow: http.HTTPFlow, item_id: str) -> http.Response:
    availability = SIM_STATE.get_item(item_id)
    SIM_STATE.log_request(route="/ip/", item_id=item_id, availability=availability.value)
    # Track the last-fetched item so cart/checkout fixtures can be
    # personalized with the SKU the bot is actually trying to buy.
    SIM_STATE.last_ip_item_id = item_id

    if availability == ItemAvailability.QUEUED:
        # Tricky case: the bot's stock_monitor does fetch('/ip/<sku>') from
        # within Tab 1, which doesn't run scripts — so a meta-refresh or JS
        # redirect on the response body won't redirect the fetch. The bot
        # needs the fetch to return IN_STOCK-shaped data so the stock signal
        # fires; THEN when the bot navigates to /ip/<sku> as a real browser
        # navigation, the response script redirects to /qp.
        #
        # If the queue has already admitted (state == "valid"), short-circuit
        # to a normal IN_STOCK response so the bot can proceed to ATC.
        queue = next(iter(SIM_STATE.queues.values()), None)
        if queue is None:
            queue = QueueScenario(item_id=item_id)
            SIM_STATE.set_queue(queue)
        if queue.current_state() == "valid":
            return _html_response(_personalize_fixture("ip_in_stock.html", item_id), flow=flow)

        # Still queued — return IN_STOCK HTML with a JS redirect to /qp at
        # the end. fetch() doesn't execute scripts (it just reads the body),
        # so the bot's stock_monitor parses __NEXT_DATA__ → IN_STOCK.
        # Browser navigation does execute the script and redirects to /qp,
        # putting the bot in the queue handler.
        qpdata = build_qpdata_url(queue.queue_id, item_id)
        body = _personalize_fixture("ip_in_stock.html", item_id).decode("utf-8")
        redirect_script = (
            f'<script>'
            f'window.location.replace("/qp?qpdata={qpdata}");'
            f'</script>'
        )
        body = body.replace("</body>", redirect_script + "</body>")
        return _html_response(body.encode("utf-8"), flow=flow)

    # Substitute the requested item_id into the fixture HTML's usItemId
    # field. Otherwise the bot's _extract_product() rejects the response
    # because product.usItemId != requested item_id.
    if availability == ItemAvailability.IN_STOCK:
        return _html_response(_personalize_fixture("ip_in_stock.html", item_id), flow=flow)
    if availability == ItemAvailability.THIRD_PARTY:
        return _html_response(_personalize_fixture("ip_third_party.html", item_id), flow=flow)
    # default: OOS
    return _html_response(_personalize_fixture("ip_oos.html", item_id), flow=flow)


def _route_qp(flow: http.HTTPFlow) -> http.Response:
    SIM_STATE.log_request(route="/qp", url=flow.request.url)
    return _html_response(SimState.load_fixture("qp_pending.html"), flow=flow)


def _personalize_fixture(template_name: str, item_id: Optional[str]) -> bytes:
    """Replace the canonical fixture usItemId with the SKU the bot is buying.
    Falls back to leaving the fixture as-is when item_id is unknown.
    """
    raw = SimState.load_fixture(template_name).decode("utf-8")
    if item_id:
        raw = raw.replace('"usItemId":"19012610850"', f'"usItemId":"{item_id}"')
    return raw.encode("utf-8")


def _route_cart(flow: http.HTTPFlow) -> http.Response:
    """The /cart page — serves a populated cart so WalmartHybridCheckout.
    read_cart_context() can extract cartId + lineItems from __NEXT_DATA__.
    """
    SIM_STATE.log_request(route="/cart", url=flow.request.url)
    return _html_response(
        _personalize_fixture("cart_populated.html", SIM_STATE.last_ip_item_id),
        flow=flow,
    )


def _route_checkout(flow: http.HTTPFlow) -> http.Response:
    """The /checkout page. Bot navigates here from /cart, then the
    hybrid flow takes over via GraphQL mutations.
    """
    SIM_STATE.log_request(route="/checkout", url=flow.request.url)
    return _html_response(
        _personalize_fixture("checkout_page.html", SIM_STATE.last_ip_item_id),
        flow=flow,
    )


def _route_ticket_api(flow: http.HTTPFlow) -> http.Response:
    parsed = urlparse(flow.request.url)
    params = parse_qs(parsed.query)
    queue_id = (params.get("queue") or [None])[0]
    endpoint = parsed.path.lstrip("/")

    if queue_id is None:
        return _json_response({"error": "missing queue param"}, status=400, flow=flow)

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
        return _json_response({"tickets": [body]}, flow=flow)
    return _json_response(body, flow=flow)


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
        }, flow=flow)

    # Hash is known OR full query was provided — return canned success
    # Operation-specific success body
    return _graphql_success_body(op_name, hash_value, flow)


def _graphql_success_body(op_name: str, hash_value: str,
                          flow: Optional[http.HTTPFlow] = None) -> http.Response:
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
    return _json_response(body, flow=flow)


def _route_getkey(flow: http.HTTPFlow) -> http.Response:
    """PIE.js public-key endpoint. Returns the JS body wrapped as a script.

    Serves the sim's actual RSA modulus (from SIM_STATE.pie_keypair) so the
    bot's encrypted CVV can be decrypted by the sim's matching private key.
    """
    SIM_STATE.log_request(route="getkey.js", url=flow.request.url)
    headers = {"Content-Type": "application/javascript"}
    headers.update(_cors_headers(flow))

    modulus_hex = SIM_STATE.get_pie_modulus_hex()
    pie_obj = {
        "K0c": SIM_STATE.pie_public_key_id,
        "key_id": SIM_STATE.pie_public_key_id,
        "phase": SIM_STATE.pie_phase,
        "L": 2048,
        "k": modulus_hex,
        "c1": 1,
    }
    body = "var PIE = " + json.dumps(pie_obj) + ";"
    return http.Response.make(200, body.encode("utf-8"), headers)


def _route_pie_submit(flow: http.HTTPFlow) -> http.Response:
    """Accept the PIE-encrypted CVV payload, decrypt with sim's test private
    key, log the plaintext for test assertions.

    The bot's submit_cvv_via_pie POSTs {encryptedSecurityCode, key_id, phase,
    ...} to this route. We decrypt encryptedSecurityCode and store the
    plaintext in SIM_STATE.pie_decrypted so the test can assert which CVV
    was actually transmitted (without inspecting the wire bytes directly).
    """
    try:
        body_json = json.loads(flow.request.text or "{}")
    except (json.JSONDecodeError, TypeError):
        body_json = {}

    encrypted_cvv = body_json.get("encryptedSecurityCode")
    key_id = body_json.get("key_id")
    phase = body_json.get("phase")

    decrypted_plaintext: Optional[str] = None
    decrypt_error: Optional[str] = None
    if encrypted_cvv:
        try:
            decrypted_plaintext = SIM_STATE.decrypt_pie_payload(encrypted_cvv)
            if decrypted_plaintext is None:
                decrypt_error = "decrypt_returned_none"
        except Exception as e:
            decrypt_error = f"{type(e).__name__}: {e}"

    SIM_STATE.pie_decrypted.append({
        "key_id": key_id,
        "phase": phase,
        "ciphertext_hex_len": len(encrypted_cvv) if encrypted_cvv else 0,
        "decrypted": decrypted_plaintext,
        "error": decrypt_error,
        "t": time.time() - SIM_STATE.start_time,
    })
    SIM_STATE.log_request(
        route="pie_submit", path=flow.request.path,
        has_encryptedSecurityCode=bool(encrypted_cvv),
        decrypted_ok=decrypted_plaintext is not None,
    )

    if decrypted_plaintext is None:
        return _json_response(
            {"error": "ciphertext could not be decrypted", "detail": decrypt_error},
            status=400, flow=flow,
        )
    return _json_response(
        {"cardToken": "test-card-token", "status": "ok"}, flow=flow,
    )


# ── /__sim__/ control plane ──────────────────────────────────────────────
# Test process drives the subprocess state by POSTing to a magic host:
# http://__sim__.walmart.local/<endpoint>. The host is a sentinel that
# only routes when proxied; mitmproxy never forwards it anywhere real.


def _route_sim_control(flow: http.HTTPFlow) -> http.Response:
    """Control plane for tests to script SimState in the subprocess.

    Endpoints (all POST application/json except as noted):
      POST /__sim__/reset          — clear all state
      POST /__sim__/item           — body: {item_id, availability}
                                     availability: in_stock | oos | third_party | queued
      POST /__sim__/queue          — body: QueueScenario JSON (queue_id,
                                     item_id, initial_state, initial_likelihood,
                                     state_transitions, likelihood_transitions,
                                     next_refresh_relative_time_ms)
      POST /__sim__/hash           — body: {op_name, known_hashes: [...], force_miss}
      GET  /__sim__/log            — returns SIM_STATE.request_log as JSON
      GET  /__sim__/graphql_log    — returns per-op GraphQL hash-scenario request_log
    """
    path = flow.request.path
    method = flow.request.method

    if path == "/__sim__/reset" and method == "POST":
        SIM_STATE.reset()
        return _json_response({"ok": True})

    if path == "/__sim__/item" and method == "POST":
        try:
            body = json.loads(flow.request.text or "{}")
            item_id = str(body["item_id"])
            avail_str = str(body["availability"]).lower()
            avail = ItemAvailability(avail_str)
            SIM_STATE.set_item(item_id, avail)
            return _json_response({"ok": True, "item_id": item_id,
                                   "availability": avail.value})
        except Exception as e:
            return _json_response({"error": str(e)}, status=400)

    if path == "/__sim__/queue" and method == "POST":
        try:
            body = json.loads(flow.request.text or "{}")
            scenario = QueueScenario(
                queue_id=str(body.get("queue_id", "qa484c0ebd7014")),
                item_id=str(body.get("item_id", "19012610850")),
                initial_state=QueueState(body.get("initial_state", "pending")),
                initial_likelihood=AdmissionLikelihood(
                    body.get("initial_likelihood", "likely")
                ),
                next_refresh_relative_time_ms=int(
                    body.get("next_refresh_relative_time_ms", 2000)
                ),
                state_transitions=[
                    (int(t[0]), QueueState(t[1]))
                    for t in body.get("state_transitions", [])
                ],
                likelihood_transitions=[
                    (int(t[0]), AdmissionLikelihood(t[1]))
                    for t in body.get("likelihood_transitions", [])
                ],
            )
            SIM_STATE.set_queue(scenario)
            return _json_response({"ok": True, "queue_id": scenario.queue_id})
        except Exception as e:
            return _json_response({"error": str(e)}, status=400)

    if path == "/__sim__/hash" and method == "POST":
        try:
            from walmart.sim.state import HashScenario
            body = json.loads(flow.request.text or "{}")
            op_name = str(body["op_name"])
            scenario = HashScenario(
                known_hashes=set(body.get("known_hashes", [])),
                force_miss=bool(body.get("force_miss", False)),
            )
            SIM_STATE.set_hash_scenario(op_name, scenario)
            return _json_response({"ok": True, "op_name": op_name})
        except Exception as e:
            return _json_response({"error": str(e)}, status=400)

    if path == "/__sim__/log" and method == "GET":
        return _json_response({"log": SIM_STATE.request_log})

    if path == "/__sim__/graphql_log" and method == "GET":
        out = {
            op_name: scenario.requests_seen
            for op_name, scenario in SIM_STATE.hashes.items()
        }
        return _json_response({"graphql": out})

    if path == "/__sim__/pie_decrypted" and method == "GET":
        # Tests read this to verify which CVVs (plaintexts) the bot
        # actually transmitted via the PIE-encrypted submission path
        return _json_response({"pie_decrypted": SIM_STATE.pie_decrypted})

    if path == "/__sim__/fp_report" and method == "POST":
        # The fingerprint_probe.html page POSTs its findings here. Tests
        # then GET /__sim__/fp_reports to inspect what real Chrome reported.
        try:
            report = json.loads(flow.request.text or "{}")
            SIM_STATE.fp_reports.append(report)
            return _json_response({"ok": True, "report_count": len(SIM_STATE.fp_reports)})
        except Exception as e:
            return _json_response({"error": str(e)}, status=400)

    if path == "/__sim__/fp_reports" and method == "GET":
        return _json_response({"fp_reports": SIM_STATE.fp_reports})

    if path == "/__sim__/ping" and method == "GET":
        # Sentinel for harness to confirm sim is up
        return _json_response({"ok": True, "sim": "walmart"})

    return _json_response({"error": "unknown endpoint", "path": path}, status=404)


# ── mitmproxy hook ───────────────────────────────────────────────────────


def request(flow: http.HTTPFlow) -> None:
    """Single request hook. Dispatches by URL pattern."""
    url = flow.request.url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    method = flow.request.method or "GET"

    # CORS preflight — respond OPTIONS with permissive headers so
    # cross-origin fetches (walmart.com → api.waiting-room.walmart.com) work.
    if method == "OPTIONS":
        flow.response = http.Response.make(
            204, b"", _cors_headers(flow),
        )
        return

    # Serve the fingerprint probe HTML to Chrome. Reachable as
    # https://www.walmart.com/__fp_probe__/ so it's same-origin with the
    # /__sim__/fp_report endpoint Chrome posts back to (no CORS issues).
    if path == "/__fp_probe__/" or path == "/__fp_probe__":
        flow.response = _html_response(
            SimState.load_fixture("fingerprint_probe.html"), flow=flow,
        )
        return

    # /__sim__/ control plane — test harness POSTs scenario configs here.
    # Use sentinel host "sim.local" so tests are unambiguous about routing
    # control requests through the proxy (not via direct localhost socket).
    if path.startswith("/__sim__/"):
        flow.response = _route_sim_control(flow)
        return

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
        if path == "/cart" or path.startswith("/cart?") or path.startswith("/cart/"):
            flow.response = _route_cart(flow)
            return
        if path == "/checkout" or path.startswith("/checkout?") or path.startswith("/checkout/"):
            flow.response = _route_checkout(flow)
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

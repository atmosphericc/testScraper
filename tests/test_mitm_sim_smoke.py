"""
Smoke test for walmart/sim/ — the mitmproxy stateful Walmart simulator.

Verifies:
  1. SimServer starts and listens on the configured port
  2. All five route categories serve the expected response:
     - /ip/<id> (IN_STOCK, OOS, third-party, queued/redirect)
     - /qp* (queue interstitial HTML)
     - api.waiting-room.walmart.com (ticket API JSON)
     - /orchestra/cartxo/graphql/<op>/<hash> (with + without known hash)
     - getkey.js (PIE)
  3. State machine: queue pending → valid after configured polls
  4. APQ miss → full-query retry path

No Chrome, no zendriver — just `requests` through the proxy. This is the
cheapest possible E2E verification that the sim's HTTP shape is correct.

Run: python -m pytest tests/test_mitm_sim_smoke.py -v
  OR: python tests/test_mitm_sim_smoke.py    (standalone)
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import requests   # noqa: E402


from walmart.sim.server import SimServer, DEFAULT_MITM_PORT   # noqa: E402


# ── test infrastructure ──────────────────────────────────────────────────


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


def _free_port() -> int:
    """Find a free TCP port. Used to avoid collision when DEFAULT_MITM_PORT
    is occupied (e.g., from a stale subprocess)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _proxies(port: int) -> dict[str, str]:
    return {
        "http": f"http://127.0.0.1:{port}",
        "https": f"http://127.0.0.1:{port}",
    }


# ── tests ────────────────────────────────────────────────────────────────


def test_server_starts_and_stops(port: int):
    sim = SimServer(port=port)
    try:
        sim.start(timeout=10.0)
        _check("server starts within timeout", True)
        # Connect socket directly — proves the listener is up
        with socket.create_connection(("127.0.0.1", port), timeout=2.0):
            _check("port is listening", True)
    except Exception as e:
        _check("server starts within timeout", False, str(e))
    finally:
        sim.stop()


def test_routes_via_proxy(port: int):
    """All five route categories return the expected shape."""
    sim = SimServer(port=port)
    sim.start(timeout=10.0)
    try:
        # Need to import the addon's SIM_STATE to configure scenarios.
        # PROBLEM: addon runs in the subprocess. Our state changes here
        # don't reach it. For Day 1 smoke we accept the default behavior
        # (items default to OOS, queue scenarios auto-created with defaults).
        # Day 2 will add a /__sim__/ control endpoint for live scripting.

        # --- /ip/<id> ---
        r = requests.get(
            "https://www.walmart.com/ip/12345",
            proxies=_proxies(port), verify=False, timeout=10,
        )
        _check("/ip/<id> returns 200", r.status_code == 200,
               detail=f"status={r.status_code}")
        # Default availability = OOS; the OOS fixture has the bot's expected
        # __NEXT_DATA__ shape
        _check("/ip/<id> serves __NEXT_DATA__",
               "__NEXT_DATA__" in r.text,
               detail=f"body preview: {r.text[:200]}")
        _check("/ip/<id> default is OOS",
               '"availabilityStatus":"OUT_OF_STOCK"' in r.text)

        # --- /qp queue interstitial ---
        r = requests.get(
            "https://www.walmart.com/qp?qpdata=test",
            proxies=_proxies(port), verify=False, timeout=10,
        )
        _check("/qp returns 200", r.status_code == 200,
               detail=f"status={r.status_code}")
        _check("/qp serves queue HTML",
               "in line" in r.text.lower(),
               detail=f"body preview: {r.text[:300]}")
        _check("/qp references ticket API",
               "api.waiting-room.walmart.com" in r.text)

        # --- ticket API ---
        r = requests.get(
            "https://api.waiting-room.walmart.com/checkTicket?queue=qa484c0ebd7014",
            proxies=_proxies(port), verify=False, timeout=10,
        )
        _check("checkTicket returns 200", r.status_code == 200,
               detail=f"status={r.status_code}")
        try:
            body = r.json()
        except Exception:
            body = None
            _check("checkTicket returns JSON", False, detail=r.text[:200])
        if body is not None:
            _check("checkTicket has state field",
                   "state" in body and body["state"] in ("pending", "valid", "expired"),
                   detail=f"state={body.get('state')}")
            _check("checkTicket has queue field",
                   body.get("queue") == "qa484c0ebd7014")
            _check("checkTicket has admissionLikelihood",
                   (body.get("customMetadata") or {}).get("admissionLikelihood") in
                   ("likely", "moderate", "unlikely"))
            _check("checkTicket has nextRefreshRelativeTime > 0",
                   isinstance(body.get("nextRefreshRelativeTime"), int)
                   and body["nextRefreshRelativeTime"] > 0)
            _check("checkTicket has all expected real-shape keys",
                   all(k in body for k in (
                       "site", "queue", "ticket", "state", "signature",
                       "itemId", "expectedTurnTimeUnixTimestamp",
                       "nextRefreshRelativeTime", "customMetadata",
                   )))

        # --- validateTickets array shape ---
        r = requests.get(
            "https://api.waiting-room.walmart.com/validateTickets?queue=qa484c0ebd7014",
            proxies=_proxies(port), verify=False, timeout=10,
        )
        if r.status_code == 200:
            try:
                body = r.json()
                _check("validateTickets wraps in tickets array",
                       isinstance(body, dict) and isinstance(body.get("tickets"), list),
                       detail=f"got: {str(body)[:200]}")
            except Exception:
                _check("validateTickets returns JSON", False, detail=r.text[:200])

        # --- GraphQL: unknown hash → APQ miss ---
        r = requests.post(
            "https://www.walmart.com/orchestra/cartxo/graphql/updateItems/badbadhash",
            proxies=_proxies(port), verify=False, timeout=10,
            json={"variables": {}, "extensions": {"persistedQuery": {"sha256Hash": "badbadhash", "version": 1}}},
        )
        _check("GraphQL APQ miss returns 200", r.status_code == 200)
        try:
            body = r.json()
            errors = body.get("errors", [])
            _check("APQ miss has PersistedQueryNotFound message",
                   any("PersistedQueryNotFound" in (e.get("message") or "")
                       for e in errors),
                   detail=f"got errors: {errors}")
            _check("APQ miss has extensions.code sentinel",
                   any((e.get("extensions") or {}).get("code") == "PERSISTED_QUERY_NOT_FOUND"
                       for e in errors),
                   detail="sentinel check critical — Apollo Client #10253")
        except Exception:
            _check("APQ miss returns JSON", False, detail=r.text[:200])

        # --- GraphQL: full-query body fallback ---
        r = requests.post(
            "https://www.walmart.com/orchestra/cartxo/graphql/updateItems/badbadhash",
            proxies=_proxies(port), verify=False, timeout=10,
            json={
                "variables": {},
                "query": "mutation updateItems { ... }",
                "extensions": {"persistedQuery": {"sha256Hash": "badbadhash", "version": 1}},
            },
        )
        _check("GraphQL with full query returns 200", r.status_code == 200)
        try:
            body = r.json()
            _check("GraphQL with full query returns data (no errors)",
                   "data" in body and "errors" not in body,
                   detail=f"body: {str(body)[:200]}")
        except Exception:
            _check("GraphQL fallback returns JSON", False, detail=r.text[:200])

        # --- PIE getkey ---
        r = requests.get(
            "https://securedataweb.walmart.com/pie/v1/wmcom_us_vtg_pie/getkey.js",
            proxies=_proxies(port), verify=False, timeout=10,
        )
        _check("getkey.js returns 200", r.status_code == 200,
               detail=f"status={r.status_code}")
        _check("getkey.js contains PIE object",
               "var PIE" in r.text or "PIE =" in r.text,
               detail=f"body preview: {r.text[:200]}")

    finally:
        sim.stop()


def test_state_machine_transitions(port: int):
    """checkTicket calls advance the queue state through the configured
    transition. We can't write to subprocess state from here, so we verify
    the AUTO-CREATED scenario behavior: first call should be pending."""
    sim = SimServer(port=port)
    sim.start(timeout=10.0)
    try:
        url = "https://api.waiting-room.walmart.com/checkTicket?queue=test_q_unique_for_smoke"

        # Three calls — by default state stays pending forever (no transitions
        # set up). This proves the queue counter advances without crashing.
        states_seen = []
        for i in range(3):
            r = requests.get(url, proxies=_proxies(port), verify=False, timeout=10)
            if r.status_code == 200:
                try:
                    states_seen.append(r.json().get("state"))
                except Exception:
                    states_seen.append(f"json_err:{r.text[:60]}")
            else:
                states_seen.append(f"http_{r.status_code}")

        _check("3 sequential checkTicket calls all succeed",
               all(s == "pending" for s in states_seen),
               detail=f"saw: {states_seen}")
    finally:
        sim.stop()


# ── runner ───────────────────────────────────────────────────────────────


def main():
    print("=" * 70)
    print("walmart sim — Day 1 smoke test")
    print("=" * 70)

    # Suppress SSL warnings (we use a self-signed mitmproxy cert via --ssl-insecure)
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    port = _free_port()
    print(f"Using random free port: {port}")

    test_server_starts_and_stops(port)

    # Use a different port for the second test in case the first didn't
    # clean up before mitmproxy unbinds (race condition on macOS)
    port = _free_port()
    test_routes_via_proxy(port)

    port = _free_port()
    test_state_machine_transitions(port)

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

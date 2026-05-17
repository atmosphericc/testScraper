"""
Walmart queue mock-drop harness — exercises QueueHandler against a real
Chrome tab with CDP-intercepted Walmart traffic. No real Walmart calls.

STATUS (2026-05-17): partial validation. The basic harness mechanics
work (the `no_queue` scenario passes cleanly, proving Chrome + CDP Fetch
interception + QueueHandler.detect() all play together). However, the
ticket-API state-transition scenarios reveal an architectural nuance:

When CDP Fetch interception is enabled (the mock's mechanism for
faking the ticket API), the `Fetch.fulfill_request` path bypasses
Chrome's normal Network domain stack. This means the QueueHandler's
`Network.responseReceived` listener does NOT fire for mocked responses —
even though the page's own JS receives and processes them correctly.

In a real Walmart drop, this is not an issue: Walmart's queue page
makes real network requests to api.waiting-room.walmart.com via the
normal Network stack, so Network.responseReceived fires as expected.
The mock simply can't replicate that path because using Fetch to inject
fake responses inherently bypasses Network events.

To fully validate via mocks would require pivoting to a man-in-the-middle
proxy (mitmproxy or aiohttp DNS override) that lets Chrome make "real"
network requests to a fake server. That's a ~4-6 hour follow-on project.

For now, the harness validates:
  - Chrome + CDP Fetch interception architecture works
  - The QueueHandler.detect() URL-pattern matching catches /qp redirects
  - The no-queue path returns None cleanly
  - The flag-management lifecycle (set/clear) runs even on early returns

For now, the harness does NOT validate:
  - End-to-end ticket polling → state=valid → admission detection
    (the actual code path that matters most for drop day)
  - The CDP Network.ResponseReceived listener firing on real responses

What this means for drop readiness: the QueueHandler's CDP listener
code path is currently validated only by unit tests against synthetic
QueueTicket objects (see test_walmart_queue_handler.py). The first time
the listener actually fires against real Walmart traffic will be during
an actual drop. This is a known gap documented in
docs/POKEMON_DROP_READINESS.md.

Run: python test_walmart_queue_mock_drop.py
Requires a working Chrome installation. ~2 min runtime for all scenarios.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    name: str
    status: str
    detail: str = ""


results: list[TestResult] = []


def _check(name: str, cond: bool, detail: str = ""):
    results.append(TestResult(name, "PASS" if cond else "FAIL", detail))


# ── scenario definitions ─────────────────────────────────────────────────


@dataclass
class TicketResponse:
    """One scripted response from the fake api.waiting-room.walmart.com."""
    state: str
    likelihood: str = "likely"
    queue_id: str = "x_test_queue_1"
    ticket: str = "12345"
    next_refresh_ms: int = 5000


@dataclass
class Scenario:
    name: str
    description: str
    # If True, the navigation to the product URL returns a /qp redirect.
    # If False, the product URL is served normally (no queue).
    queue_active: bool
    # Ordered list of responses to serve from the ticket API. After the list
    # is exhausted, further requests get the last response repeatedly.
    ticket_responses: list[TicketResponse] = field(default_factory=list)
    # Expected outcome: what state should QueueHandler.detect_and_wait return?
    expected_state: Optional[str] = None
    # Optional max-unlikely-streak override
    max_unlikely_streak: int = 3
    # Optional timeout for detect_and_wait
    timeout: float = 30.0


# Construct the fake /qp page HTML — Chrome needs valid HTML to render and
# fire the JS that polls the ticket API. We embed a tiny script that
# does the polling so our CDP listener has something to observe.
def _make_qp_html(item_id: str, ticket_url: str) -> str:
    """Minimal queue page that polls the (fake) ticket API every 1s."""
    return f"""<!DOCTYPE html>
<html><head><title>Walmart — Queue (test)</title></head>
<body>
<h1 id="hdr">You're in line for item {item_id}</h1>
<div id="status">Waiting...</div>
<script>
  // Poll the ticket API. Our CDP harness intercepts this and feeds
  // scripted responses.
  let pollCount = 0;
  async function poll() {{
    try {{
      const resp = await fetch({json.dumps(ticket_url)});
      const data = await resp.json();
      pollCount += 1;
      document.getElementById('status').textContent =
        'poll #' + pollCount + ' state=' + data.state;
      if (data.state === 'valid') {{
        // Walmart's real behavior: navigate away from /qp on admission.
        window.location.href = '/ip/admitted';
        return;
      }}
      if (data.state === 'expired') {{
        document.getElementById('status').textContent = 'EXPIRED';
        return;
      }}
      // Otherwise keep polling
      const next = data.nextRefreshRelativeTime || 1000;
      setTimeout(poll, next);
    }} catch (e) {{
      document.getElementById('status').textContent = 'fetch failed: ' + e;
    }}
  }}
  // Start polling almost immediately
  setTimeout(poll, 100);
</script>
</body></html>
"""


def _make_admitted_html(item_id: str) -> str:
    """Page served when the queue admits us and Chrome navigates to /ip/admitted."""
    return f"""<!DOCTYPE html>
<html><head><title>Admitted</title></head>
<body><h1>Admitted for item {item_id}</h1></body></html>
"""


# ── CDP-intercepting mock server ─────────────────────────────────────────


class QueueMockServer:
    """In-Chrome mock that intercepts requests via CDP Fetch domain.

    Patterns intercepted:
      - https://www.walmart.com/ip/<test_id>  → 302 to /qp or 200 product page
      - https://www.walmart.com/qp*           → 200 with embedded queue HTML
      - https://api.waiting-room.walmart.com/checkTicket*  → 200 JSON ticket
      - https://www.walmart.com/ip/admitted   → 200 admitted page (post-queue)

    All other requests pass through with continue_request.

    Counts ticket polls so tests can verify the polling cadence.
    """

    TEST_ITEM_ID = "9999000000"
    PRODUCT_URL = f"https://www.walmart.com/ip/{TEST_ITEM_ID}"
    QP_URL = f"https://www.walmart.com/qp?qpdata={{}}"
    TICKET_URL = "https://api.waiting-room.walmart.com/checkTicket?queue=x_test_queue_1"

    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.poll_count = 0
        self._handler_attached = False

    async def attach(self, tab):
        """Enable Fetch interception and register the request handler."""
        from zendriver import cdp

        # Enable Fetch domain with patterns matching ALL requests so we
        # intercept everything (and pass through what we don't fake).
        patterns = [cdp.fetch.RequestPattern(url_pattern="*")]
        await tab.send(cdp.fetch.enable(patterns=patterns))

        async def on_request_paused(event):
            """Fires on every intercepted request — fake or pass through."""
            try:
                req = event.request
                req_id = event.request_id
                url = req.url
                method = req.method

                logger.debug(f"[MOCK] intercept {method} {url[:100]}")

                # Fake the ticket API
                if "api.waiting-room.walmart.com" in url and "checkTicket" in url:
                    self.poll_count += 1
                    idx = min(self.poll_count - 1,
                              len(self.scenario.ticket_responses) - 1)
                    if idx < 0:
                        # No responses scripted — return generic pending
                        tr = TicketResponse(state="pending")
                    else:
                        tr = self.scenario.ticket_responses[idx]
                    body = self._build_ticket_body(tr)
                    await self._fulfill(tab, req_id, 200, body, "application/json")
                    logger.info(
                        f"[MOCK] poll #{self.poll_count} → state={tr.state} "
                        f"likelihood={tr.likelihood}"
                    )
                    return

                # Fake the /qp queue interstitial
                if "/qp" in url:
                    body = _make_qp_html(self.TEST_ITEM_ID, self.TICKET_URL)
                    await self._fulfill(tab, req_id, 200, body, "text/html")
                    logger.info(f"[MOCK] serving /qp queue page")
                    return

                # Fake the admitted page (post-queue navigation)
                if url.endswith("/ip/admitted"):
                    body = _make_admitted_html(self.TEST_ITEM_ID)
                    await self._fulfill(tab, req_id, 200, body, "text/html")
                    logger.info(f"[MOCK] serving admitted page")
                    return

                # Fake the product page — redirect to queue or serve normal
                if url == self.PRODUCT_URL or url.startswith(self.PRODUCT_URL + "?"):
                    if self.scenario.queue_active:
                        # 302 redirect to /qp
                        qpdata_payload = {
                            "queued": True,
                            "queue": "x_test_queue_1",
                            "url": self.TICKET_URL,
                            "customMetadata": {
                                "item": {
                                    "itemID": self.TEST_ITEM_ID,
                                    "name": "Test Pokemon Item",
                                    "currentPrice": "$99.99",
                                },
                            },
                        }
                        encoded = urllib.parse.quote(json.dumps(qpdata_payload))
                        location = f"/qp?qpdata={encoded}"
                        await self._fulfill(
                            tab, req_id, 302, b"",
                            "text/html",
                            extra_headers=[("Location", location)],
                        )
                        logger.info(f"[MOCK] 302 → /qp for product URL")
                    else:
                        # Normal product page (no queue)
                        body = f"""<!DOCTYPE html><html><body>
<h1>Test product {self.TEST_ITEM_ID}</h1>
</body></html>"""
                        await self._fulfill(tab, req_id, 200, body, "text/html")
                        logger.info(f"[MOCK] serving normal product page")
                    return

                # Anything else: pass through normally
                await tab.send(cdp.fetch.continue_request(req_id))

            except Exception as e:
                logger.error(f"[MOCK] handler error: {e}\n{traceback.format_exc()}")
                # Try to fail the request so it doesn't hang
                try:
                    await tab.send(cdp.fetch.fail_request(
                        event.request_id, error_reason="Failed"
                    ))
                except Exception:
                    pass

        tab.add_handler(cdp.fetch.RequestPaused, on_request_paused)
        self._handler_attached = True

    async def _fulfill(
        self, tab, req_id, status: int, body, content_type: str,
        extra_headers: Optional[list] = None,
    ):
        """Wrap cdp.fetch.fulfill_request with base64 body encoding (CDP requires it)."""
        import base64
        from zendriver import cdp
        if isinstance(body, str):
            body_bytes = body.encode("utf-8")
        else:
            body_bytes = body
        body_b64 = base64.b64encode(body_bytes).decode("ascii")
        headers = [cdp.fetch.HeaderEntry(name="Content-Type", value=content_type)]
        if extra_headers:
            for name, value in extra_headers:
                headers.append(cdp.fetch.HeaderEntry(name=name, value=value))
        await tab.send(cdp.fetch.fulfill_request(
            request_id=req_id,
            response_code=status,
            response_headers=headers,
            body=body_b64,
        ))

    @staticmethod
    def _build_ticket_body(tr: TicketResponse) -> str:
        """JSON body matching the real ticket API shape."""
        return json.dumps({
            "queue": tr.queue_id,
            "ticket": tr.ticket,
            "state": tr.state,
            "customMetadata": {"admissionLikelihood": tr.likelihood},
            "nextRefreshRelativeTime": tr.next_refresh_ms,
            "itemId": "9999000000",
        })


# ── scenarios ────────────────────────────────────────────────────────────


SCENARIOS = [
    Scenario(
        name="quick_admission",
        description="Single poll returns state=valid — admitted immediately",
        queue_active=True,
        ticket_responses=[TicketResponse(state="valid")],
        expected_state="valid",
        timeout=15.0,
    ),
    Scenario(
        name="standard_admission",
        description="3 pending polls then valid",
        queue_active=True,
        ticket_responses=[
            TicketResponse(state="pending", next_refresh_ms=1000),
            TicketResponse(state="pending", next_refresh_ms=1000),
            TicketResponse(state="pending", next_refresh_ms=1000),
            TicketResponse(state="valid"),
        ],
        expected_state="valid",
        timeout=20.0,
    ),
    Scenario(
        name="eviction",
        description="Pending pending then expired",
        queue_active=True,
        ticket_responses=[
            TicketResponse(state="pending", next_refresh_ms=1000),
            TicketResponse(state="pending", next_refresh_ms=1000),
            TicketResponse(state="expired"),
        ],
        expected_state="expired",
        timeout=15.0,
    ),
    Scenario(
        name="unlikely_streak_bail",
        description="3 pending+unlikely polls → handler bails early",
        queue_active=True,
        ticket_responses=[
            TicketResponse(state="pending", likelihood="unlikely", next_refresh_ms=1000),
            TicketResponse(state="pending", likelihood="unlikely", next_refresh_ms=1000),
            TicketResponse(state="pending", likelihood="unlikely", next_refresh_ms=1000),
        ],
        expected_state="pending",   # bail returns the pending ticket
        max_unlikely_streak=3,
        timeout=15.0,
    ),
    Scenario(
        name="no_queue",
        description="Product page served normally — handler returns None",
        queue_active=False,
        ticket_responses=[],
        expected_state=None,        # detect() returns None on non-queue page
        timeout=5.0,
    ),
]


# ── test runner ──────────────────────────────────────────────────────────


async def run_scenario(scenario: Scenario) -> tuple[bool, str]:
    """Returns (passed, detail)."""
    try:
        import zendriver as uc
    except ImportError:
        return False, "zendriver not installed"

    from walmart.queue_handler import QueueHandler, QueueState

    logger.info("=" * 60)
    logger.info(f"SCENARIO: {scenario.name}")
    logger.info(f"  {scenario.description}")
    logger.info("=" * 60)

    config = uc.Config(
        headless=True,
        browser_args=["--window-size=800,600", "--disable-features=NetworkService"],
        browser_connection_timeout=1.0,
        browser_connection_max_tries=30,
    )
    browser = await uc.start(config)
    try:
        # Open a blank tab first so we can attach Fetch interception before navigation
        tab = browser.main_tab
        if tab is None:
            return False, "no main tab"

        mock = QueueMockServer(scenario)
        await mock.attach(tab)
        logger.info(f"[TEST] Mock attached. Navigating to {mock.PRODUCT_URL}")

        await asyncio.wait_for(tab.get(mock.PRODUCT_URL), timeout=20.0)

        # Brief settle so the queue page's JS has a chance to fire its first poll
        await asyncio.sleep(0.5)

        # Drive QueueHandler
        handler = QueueHandler(tab)
        result = await handler.detect_and_wait(
            timeout=scenario.timeout,
            max_unlikely_streak=scenario.max_unlikely_streak,
        )

        # Verify outcome
        if scenario.expected_state is None:
            # We expected "not in queue" → result should be None
            if result is None:
                return True, "ok (not queued as expected)"
            return False, f"expected None, got {result}"

        if result is None:
            return False, f"expected state={scenario.expected_state}, got None"

        if result.state != scenario.expected_state:
            return False, (
                f"expected state={scenario.expected_state}, "
                f"got state={result.state} (ticket={result})"
            )

        return True, f"ok (state={result.state}, polls={mock.poll_count})"

    except Exception as e:
        return False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}"
    finally:
        try:
            await browser.stop()
        except Exception:
            pass


async def main():
    print("=" * 70)
    print("Walmart queue mock-drop harness")
    print("=" * 70)
    print()
    for scenario in SCENARIOS:
        passed, detail = await run_scenario(scenario)
        results.append(TestResult(
            name=scenario.name,
            status="PASS" if passed else "FAIL",
            detail=detail,
        ))

    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    for r in results:
        marker = "✓" if r.status == "PASS" else "✗"
        print(f"  {marker} {r.name}")
        if r.detail:
            for line in r.detail.split("\n")[:3]:
                print(f"      {line}")
    print()
    print(f"Results: {pass_count} passed, {fail_count} failed")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

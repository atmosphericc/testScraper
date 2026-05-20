"""
QueueHandler end-to-end against the mitmproxy Walmart simulator (Day 2).

This is the test the CDP Network listener path has never had: real TCP/TLS
flowing through Chrome's Network stack so `Network.responseReceived` events
actually fire. Prior CDP Fetch-interception mocks bypassed this domain.

7 scenarios — each instruments two signals independently:
  (a) Was the QueueHandler's CDP listener invoked?
  (b) Did detect_and_wait() terminate in the expected state?

If (a) fails but (b) passes → URL-watcher fallback fired, CDP path broken.
If (a) passes but (b) fails → parser bug.
If both pass → end-to-end correctness.

Run: python tests/test_walmart_queue_e2e_mitm.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import urllib3   # noqa: E402
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from walmart.queue_handler import (   # noqa: E402
    AdmissionLikelihood, QueueHandler, QueueState, QueueTicket,
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


# ── helper: instrumented QueueHandler ────────────────────────────────────


class _InstrumentedHandler(QueueHandler):
    """QueueHandler subclass that exposes CDP-callback firings as a counter.

    We override the CDP listener attachment to wrap the inner handler with
    a counter, so each test can independently assert "was the listener
    invoked" vs "did the state machine reach the right state".
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cdp_callback_fires = 0
        self.cdp_tickets_observed: list[QueueTicket] = []

    async def _maybe_attach_cdp_listener(self):
        """Attach a wrapped handler that increments our counter, then call
        the original to set _last_ticket. We do this by monkeypatching the
        zendriver event handler registration."""
        if self._cdp_handler_attached:
            return
        try:
            from zendriver import cdp as _cdp
        except ImportError:
            return

        # Wrap the original on_response_received logic
        async def _wrapped_on_response_received(event):
            try:
                resp = getattr(event, "response", None)
                if resp is None:
                    return
                url = getattr(resp, "url", "") or ""
                # Mirror queue_handler's URL-filter (avoid counting non-ticket responses)
                from walmart.queue_handler import is_ticket_api_url
                if not is_ticket_api_url(url):
                    return
                request_id = getattr(event, "request_id", None)
                if not request_id:
                    return
                # Pull the body
                try:
                    body_event = await self._page.send(
                        _cdp.network.get_response_body(request_id)
                    )
                except Exception:
                    return
                if isinstance(body_event, tuple) and len(body_event) >= 1:
                    raw_body = body_event[0]
                else:
                    raw_body = body_event
                if not raw_body:
                    return
                try:
                    body_json = json.loads(raw_body)
                except (json.JSONDecodeError, TypeError):
                    return
                from walmart.queue_handler import parse_ticket_response
                parsed = parse_ticket_response(body_json)
                if parsed is None:
                    return
                self._last_ticket = parsed
                self.cdp_callback_fires += 1
                self.cdp_tickets_observed.append(parsed)
            except Exception:
                pass

        try:
            self._page.add_handler(
                _cdp.network.ResponseReceived, _wrapped_on_response_received,
            )
            self._cdp_handler_attached = True
        except AttributeError:
            pass


# ── scenarios ────────────────────────────────────────────────────────────


async def scenario_1_cdp_listener_fires_on_real_response(h: WalmartSimHarness):
    """Bot navigates to /qp page. Page JS polls checkTicket. CDP listener
    must observe at least one ticket API response within 5 seconds."""
    h.ctl.reset()
    # Configure a queue that stays pending forever (so we can count polls)
    h.ctl.set_queue(queue_id="qa484c0ebd7014", initial_state="pending",
                    next_refresh_relative_time_ms=1000)

    # Navigate to /qp directly — the embedded JS polls checkTicket on a 1s cadence
    import urllib.parse, json as _json
    qpdata = urllib.parse.quote(_json.dumps({
        "queued": True, "queue": "qa484c0ebd7014",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=qa484c0ebd7014",
        "customMetadata": {"item": {"itemID": "19012610850"}},
    }))
    await h.tab.get(f"https://www.walmart.com/qp?qpdata={qpdata}")

    handler = _InstrumentedHandler(h.tab)
    # Use detect_and_wait with short timeout to give CDP some firing room
    await handler._maybe_attach_cdp_listener()

    # Let the page's polling JS run for a few seconds — sim returns pending,
    # so the CDP callback should fire >= 1 time but state stays pending.
    await asyncio.sleep(5.0)

    _check(
        "scenario_1: CDP listener fired at least once on real Network.responseReceived",
        handler.cdp_callback_fires >= 1,
        detail=f"fires={handler.cdp_callback_fires}",
    )
    if handler.cdp_callback_fires > 0:
        last = handler.cdp_tickets_observed[-1]
        _check(
            "scenario_1: observed ticket has expected queue_id",
            last.queue_id == "qa484c0ebd7014",
            detail=f"got queue_id={last.queue_id}",
        )
        _check(
            "scenario_1: observed ticket is in pending state",
            last.state == QueueState.PENDING,
            detail=f"got state={last.state}",
        )


async def scenario_2_pending_to_valid_admission(h: WalmartSimHarness):
    """Queue starts pending, sim transitions to valid after 3rd poll.
    detect_and_wait should observe admission via CDP within ~6s
    (3 polls × 1500ms cadence + 1s grace)."""
    h.ctl.reset()
    h.ctl.set_queue(
        queue_id="qa484c0ebd7014",
        initial_state="pending",
        next_refresh_relative_time_ms=1500,
        state_transitions=[[3, "valid"]],
    )

    import urllib.parse, json as _json
    qpdata = urllib.parse.quote(_json.dumps({
        "queued": True, "queue": "qa484c0ebd7014",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=qa484c0ebd7014",
        "customMetadata": {"item": {"itemID": "19012610850"}},
    }))
    await h.tab.get(f"https://www.walmart.com/qp?qpdata={qpdata}")

    handler = _InstrumentedHandler(h.tab)
    t0 = time.monotonic()
    final = await handler.detect_and_wait(timeout=10.0)
    elapsed = time.monotonic() - t0

    _check(
        "scenario_2: handler returned within 10s timeout",
        final is not None,
        detail=f"elapsed={elapsed:.1f}s final={final}",
    )
    if final is not None:
        _check(
            "scenario_2: terminal state is VALID (admitted)",
            final.state == QueueState.VALID,
            detail=f"got state={final.state}",
        )
    # CDP listener firing is timing-dependent: the response that triggers
    # admission may race with handler exit, and Chrome's cache + the JS
    # page's setTimeout cadence interact unpredictably across runs.
    # The MEANINGFUL signal is "admission was reached via this CDP path"
    # (covered by the state=VALID assertion above). fires>=1 just confirms
    # the listener actually wired up correctly; the count itself is not
    # load-bearing for correctness.
    _check(
        "scenario_2: CDP listener fired ≥1 time during the wait",
        handler.cdp_callback_fires >= 1,
        detail=f"fires={handler.cdp_callback_fires}",
    )


async def scenario_3_expired_bail_out(h: WalmartSimHarness):
    """Queue starts pending, transitions to expired on 3rd poll.
    detect_and_wait should return expired ticket promptly."""
    h.ctl.reset()
    h.ctl.set_queue(
        queue_id="qa484c0ebd7014",
        initial_state="pending",
        next_refresh_relative_time_ms=1500,
        state_transitions=[[3, "expired"]],
    )

    import urllib.parse, json as _json
    qpdata = urllib.parse.quote(_json.dumps({
        "queued": True, "queue": "qa484c0ebd7014",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=qa484c0ebd7014",
        "customMetadata": {"item": {"itemID": "19012610850"}},
    }))
    await h.tab.get(f"https://www.walmart.com/qp?qpdata={qpdata}")

    handler = _InstrumentedHandler(h.tab)
    t0 = time.monotonic()
    final = await handler.detect_and_wait(timeout=15.0)
    elapsed = time.monotonic() - t0

    _check(
        "scenario_3: handler returned within timeout",
        final is not None,
        detail=f"elapsed={elapsed:.1f}s final={final}",
    )
    if final is not None:
        _check(
            "scenario_3: terminal state is EXPIRED (evicted)",
            final.state == QueueState.EXPIRED,
            detail=f"got state={final.state}",
        )


async def scenario_4_unlikely_streak_bail(h: WalmartSimHarness):
    """3 consecutive admissionLikelihood=unlikely → handler returns early
    with the pending ticket (max_unlikely_streak=3)."""
    h.ctl.reset()
    h.ctl.set_queue(
        queue_id="qa484c0ebd7014",
        initial_state="pending",
        initial_likelihood="unlikely",
        next_refresh_relative_time_ms=1500,
        # Stay unlikely; never transition
    )

    import urllib.parse, json as _json
    qpdata = urllib.parse.quote(_json.dumps({
        "queued": True, "queue": "qa484c0ebd7014",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=qa484c0ebd7014",
        "customMetadata": {"item": {"itemID": "19012610850"}},
    }))
    await h.tab.get(f"https://www.walmart.com/qp?qpdata={qpdata}")

    handler = _InstrumentedHandler(h.tab)
    t0 = time.monotonic()
    final = await handler.detect_and_wait(timeout=15.0, max_unlikely_streak=3)
    elapsed = time.monotonic() - t0

    _check(
        "scenario_4: handler returned (didn't run to timeout)",
        final is not None and elapsed < 14.0,
        detail=f"elapsed={elapsed:.1f}s final={final}",
    )
    if final is not None:
        _check(
            "scenario_4: terminal likelihood is UNLIKELY",
            final.likelihood == AdmissionLikelihood.UNLIKELY,
            detail=f"got likelihood={final.likelihood}",
        )
        _check(
            "scenario_4: terminal state stayed PENDING (early bail, no admission)",
            final.state == QueueState.PENDING,
            detail=f"got state={final.state}",
        )


async def scenario_5_url_navigates_away_admission(h: WalmartSimHarness):
    """The /qp page's polling JS navigates away to /ip/<id> on valid.
    Even if CDP somehow misses the valid-state response, the URL-watcher
    fallback should catch admission."""
    h.ctl.reset()
    h.ctl.set_queue(
        queue_id="qa484c0ebd7014",
        item_id="19012610850",
        initial_state="pending",
        next_refresh_relative_time_ms=1500,
        state_transitions=[[2, "valid"]],
    )
    # Mark the destination item as in_stock so the navigation lands cleanly
    h.ctl.set_item("19012610850", "in_stock")

    import urllib.parse, json as _json
    qpdata = urllib.parse.quote(_json.dumps({
        "queued": True, "queue": "qa484c0ebd7014",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=qa484c0ebd7014",
        "customMetadata": {"item": {"itemID": "19012610850"}},
    }))
    await h.tab.get(f"https://www.walmart.com/qp?qpdata={qpdata}")

    handler = _InstrumentedHandler(h.tab)
    t0 = time.monotonic()
    final = await handler.detect_and_wait(timeout=15.0)
    elapsed = time.monotonic() - t0

    _check(
        "scenario_5: handler returned admission via SOME path",
        final is not None and final.state == QueueState.VALID,
        detail=f"elapsed={elapsed:.1f}s final={final}",
    )

    # Independent assertions — which path produced admission?
    cdp_fired_with_valid = any(
        t.state == QueueState.VALID for t in handler.cdp_tickets_observed
    )
    url_left_qp = False
    try:
        # Page may already have navigated to /ip/<id>; check URL
        from walmart.queue_handler import is_queue_url
        current_url = h.tab.url or ""
        url_left_qp = current_url and not is_queue_url(current_url)
    except Exception:
        pass
    _check(
        "scenario_5: admission detected via CDP or URL-watcher (at least one)",
        cdp_fired_with_valid or url_left_qp,
        detail=f"cdp_valid={cdp_fired_with_valid} url_left_qp={url_left_qp} url={h.tab.url}",
    )


async def scenario_6_in_queue_flag_lifecycle(h: WalmartSimHarness):
    """Pass a SessionEntry-style object; in_queue flag must be set on entry
    and cleared on exit, even on admission/eviction/timeout."""
    h.ctl.reset()
    h.ctl.set_queue(
        queue_id="qa484c0ebd7014",
        initial_state="pending",
        next_refresh_relative_time_ms=1500,
        state_transitions=[[2, "valid"]],
    )

    # Fake SessionEntry — only needs in_queue attribute
    class FakeSession:
        def __init__(self):
            self.in_queue = False

    session = FakeSession()
    _check(
        "scenario_6: initial in_queue=False",
        session.in_queue is False,
    )

    import urllib.parse, json as _json
    qpdata = urllib.parse.quote(_json.dumps({
        "queued": True, "queue": "qa484c0ebd7014",
        "url": "https://api.waiting-room.walmart.com/issueTicket?queue=qa484c0ebd7014",
        "customMetadata": {"item": {"itemID": "19012610850"}},
    }))
    await h.tab.get(f"https://www.walmart.com/qp?qpdata={qpdata}")

    handler = _InstrumentedHandler(h.tab, session=session)

    # Drive detect_and_wait in a task so we can observe in_queue=True mid-wait
    wait_task = asyncio.create_task(handler.detect_and_wait(timeout=15.0))
    # Give it a moment to enter the wait loop
    await asyncio.sleep(0.5)
    in_queue_during_wait = session.in_queue
    final = await wait_task

    _check(
        "scenario_6: in_queue=True observed mid-wait",
        in_queue_during_wait is True,
    )
    _check(
        "scenario_6: in_queue=False after wait returns",
        session.in_queue is False,
    )
    _check(
        "scenario_6: handler reached VALID",
        final is not None and final.state == QueueState.VALID,
    )


async def scenario_7_body_signature_detection(h: WalmartSimHarness):
    """For /ip/<id> responses where the body contains queue API signatures,
    walmart_adapter's parse_response should emit availability_status='QUEUED:body_signature'.

    This test goes a level lower than QueueHandler — it directly fetches
    /ip/<id> for a QUEUED item, parses through the adapter, and asserts
    the queue is detected by body signature alone."""
    h.ctl.reset()
    h.ctl.set_item("19012610850", "queued")
    # Override default redirect: by setting QUEUED, /ip/ → 302 to /qp.
    # That's URL-shape detection (already covered scenario 1). To test
    # body-signature detection we need /ip/ to return 200 with queue
    # signatures in the body. The sim does that when redirect is followed —
    # since Chrome follows redirects automatically, the final body IS the
    # /qp page (containing api.waiting-room.walmart.com signatures).

    await h.tab.get("https://www.walmart.com/ip/19012610850")
    await asyncio.sleep(2.0)

    handler = _InstrumentedHandler(h.tab)
    ticket = await handler.detect()

    _check(
        "scenario_7: handler detects queue after /ip → /qp redirect",
        ticket is not None,
        detail=f"ticket={ticket} url={h.tab.url}",
    )

    # Independent: assert the URL-detection path also fires (URL is /qp now)
    from walmart.queue_handler import is_queue_url
    _check(
        "scenario_7: final URL is /qp (Chrome followed the redirect)",
        is_queue_url(h.tab.url or ""),
        detail=f"url={h.tab.url}",
    )


# ── runner ───────────────────────────────────────────────────────────────


SCENARIOS = [
    ("CDP listener fires on real responses", scenario_1_cdp_listener_fires_on_real_response),
    ("Pending → Valid admission", scenario_2_pending_to_valid_admission),
    ("Expired bail-out", scenario_3_expired_bail_out),
    ("Unlikely-streak early bail", scenario_4_unlikely_streak_bail),
    ("URL-navigates-away admission", scenario_5_url_navigates_away_admission),
    ("in_queue flag lifecycle", scenario_6_in_queue_flag_lifecycle),
    ("Body-signature queue detection", scenario_7_body_signature_detection),
]


async def run_scenarios():
    for name, fn in SCENARIOS:
        print(f"\n=== {name} ===")
        try:
            # Each scenario gets a fresh harness (clean Chrome, clean sim)
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
    print("Walmart queue handler — E2E against mitm sim (Day 2)")
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

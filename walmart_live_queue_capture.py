"""
One-shot live-queue capture script — 2026-05-18.

Use case: a real Walmart drop is happening RIGHT NOW. We need to capture:
  - /qp page HTML (validates our URL detection)
  - api.waiting-room.walmart.com response payloads (validates parser)
  - QueueHandler behavior against real Walmart traffic (validates listener)

Constraints:
  - Single Chrome, single bootstrapped session (s1), single SKU
  - Observe-only — does NOT attempt ATC or purchase
  - Saves captures to disk continuously (line-by-line JSONL) so a crash
    doesn't lose the data
  - Bounded run time (default 10 min)

What this script does:
  1. Loads s1's bootstrapped profile (authenticated Walmart session)
  2. Routes through s1's pinned BD proxy via local forwarder
  3. Attaches CDP Network listeners that log every response from:
     - walmart.com/qp* (the queue interstitial)
     - api.waiting-room.walmart.com/* (the ticket API)
     - walmart.com/ip/<sku> (the product page)
  4. Navigates to the SKU's product page
  5. Hands the tab to QueueHandler.detect_and_wait() in observe mode
  6. Logs everything; saves raw captures to walmart/logs/live_capture_*.jsonl
  7. Exits cleanly after admission, eviction, or timeout

Run: python walmart_live_queue_capture.py <sku> [duration_seconds]

Example: python walmart_live_queue_capture.py 19922854775 600
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("live_capture")


# ── capture writer ───────────────────────────────────────────────────────


class CaptureWriter:
    """Append-only JSONL writer with immediate-flush so crashes preserve data."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", buffering=1)   # line-buffered
        logger.info(f"Capture file: {path}")
        self.record_count = 0

    def write(self, kind: str, data: dict):
        rec = {"_kind": kind, "_ts": time.time(), **data}
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()
        self.record_count += 1

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


# ── CDP interceptors ─────────────────────────────────────────────────────


async def attach_capture_listeners(tab, writer: CaptureWriter, sku: str):
    """Listen for Network.responseReceived events on URLs of interest.
    Save full response bodies via Network.getResponseBody."""
    from zendriver import cdp

    interesting_substrings = (
        "/qp",
        "api.waiting-room.walmart.com",
        f"/ip/{sku}",
        "checkTicket",
        "issueTicket",
        "refreshTicket",
        "validateTickets",
    )

    captured_requests: dict[str, dict] = {}

    async def on_request_will_be_sent(event):
        try:
            req = event.request
            url = req.url
            if not any(s in url for s in interesting_substrings):
                return
            request_id = event.request_id
            captured_requests[request_id] = {
                "url": url,
                "method": req.method,
                "headers": dict(req.headers) if req.headers else {},
            }
            writer.write("request", {
                "request_id": str(request_id),
                "url": url,
                "method": req.method,
            })
            logger.info(f"  → {req.method} {url[:100]}")
        except Exception as e:
            logger.debug(f"request handler error: {e}")

    async def on_response_received(event):
        try:
            resp = event.response
            url = getattr(resp, "url", "") or ""
            status = getattr(resp, "status", 0)
            if not any(s in url for s in interesting_substrings):
                return
            request_id = event.request_id
            req_info = captured_requests.get(request_id, {})
            mime_type = getattr(resp, "mime_type", "") or ""
            writer.write("response_metadata", {
                "request_id": str(request_id),
                "url": url,
                "status": status,
                "mime_type": mime_type,
                "request_url": req_info.get("url"),
            })
            logger.info(f"  ← {status} {url[:100]} ({mime_type})")
            # Try to fetch the body
            try:
                body_result = await tab.send(cdp.network.get_response_body(request_id))
                if isinstance(body_result, tuple):
                    body_str = body_result[0]
                    base64_encoded = body_result[1] if len(body_result) > 1 else False
                else:
                    body_str = str(body_result) if body_result else ""
                    base64_encoded = False
                if base64_encoded and body_str:
                    try:
                        body_str = base64.b64decode(body_str).decode("utf-8", errors="replace")
                    except Exception:
                        pass
                # Truncate huge HTML bodies to save disk, but log size
                truncated = False
                if body_str and len(body_str) > 100_000:
                    truncated = True
                    body_str = body_str[:100_000]
                writer.write("response_body", {
                    "request_id": str(request_id),
                    "url": url,
                    "status": status,
                    "body": body_str,
                    "truncated": truncated,
                    "size": len(body_str) if body_str else 0,
                })
                # If JSON, log a parsed preview
                if mime_type and "json" in mime_type and body_str:
                    try:
                        parsed = json.loads(body_str)
                        logger.info(f"    JSON: {json.dumps(parsed)[:200]}")
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"getResponseBody failed: {e}")
        except Exception as e:
            logger.debug(f"response handler error: {e}")

    tab.add_handler(cdp.network.RequestWillBeSent, on_request_will_be_sent)
    tab.add_handler(cdp.network.ResponseReceived, on_response_received)
    logger.info("CDP capture listeners attached")


# ── main ─────────────────────────────────────────────────────────────────


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <sku> [duration_seconds]")
        sys.exit(2)
    sku = sys.argv[1].strip()
    duration = int(sys.argv[2]) if len(sys.argv) >= 3 else 600

    # Load proxy from active list (first entry)
    proxy_file = ROOT / "config" / "proxyIps.json"
    with open(proxy_file) as f:
        proxies = json.load(f).get("proxies") or []
    if not proxies:
        logger.error("No active proxies")
        sys.exit(1)
    proxy_url = proxies[0]
    pinned_ip = proxy_url.split("-ip-")[1].split(":")[0]
    logger.info(f"Using proxy: ip={pinned_ip}")

    # Session profile
    profile_dir = ROOT / "state" / "walmart_session_profiles" / "s1"
    if not profile_dir.exists():
        logger.error(f"Profile not found at {profile_dir} — run walmart_session_bootstrap first")
        sys.exit(1)

    # Capture output
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    capture_path = ROOT / "walmart" / "logs" / f"live_capture_{sku}_{timestamp}.jsonl"
    writer = CaptureWriter(capture_path)

    # Local forwarder + Chrome launch
    from src.stack.local_forwarder import ForwarderPool
    import zendriver as uc

    forwarder = ForwarderPool()
    local_port = 26000   # avoid colliding with resilient stack's 25000+
    upstream = forwarder.add_upstream(proxy_url, local_port)
    await forwarder.start_all()
    logger.info(f"Forwarder up on 127.0.0.1:{local_port}")

    config = uc.Config(
        user_data_dir=str(profile_dir),
        headless=False,
        browser_args=[
            "--window-size=1280,800",
            f"--proxy-server=127.0.0.1:{local_port}",
        ],
        browser_connection_timeout=1.0,
        browser_connection_max_tries=30,
    )
    browser = await uc.start(config)
    logger.info("Chrome launched with bootstrapped s1 profile")

    try:
        tab = browser.main_tab
        if tab is None:
            logger.error("no main tab")
            return 1

        # Enable Network domain so events fire
        from zendriver import cdp
        await tab.send(cdp.network.enable())

        await attach_capture_listeners(tab, writer, sku)

        # Navigate to product
        product_url = f"https://www.walmart.com/ip/{sku}"
        logger.info(f"Navigating to {product_url}")
        writer.write("nav_start", {"url": product_url})
        try:
            await asyncio.wait_for(tab.get(product_url), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Initial navigation timed out — continuing anyway")

        writer.write("nav_complete", {"url_after": tab.url or "?"})
        logger.info(f"After navigation, tab URL: {tab.url}")

        # Snapshot the page state right after navigation
        try:
            content = await asyncio.wait_for(tab.get_content(), timeout=10.0)
            writer.write("initial_page", {
                "url": tab.url,
                "size": len(content) if content else 0,
                "preview": content[:5000] if content else "",
            })
            logger.info(f"Initial page captured: {len(content) if content else 0} bytes")
        except Exception as e:
            logger.warning(f"initial page snapshot failed: {e}")

        # Hand to QueueHandler — observe-only, never ATC
        from walmart.queue_handler import QueueHandler, QueueState, AdmissionLikelihood

        handler = QueueHandler(tab)
        ticket = await handler.detect()
        if ticket is None:
            logger.info("No queue detected. May be a normal PDP or drop ended.")
            writer.write("no_queue_detected", {"url": tab.url})
            # Keep browser open briefly to capture any background activity
            await asyncio.sleep(30)
        else:
            logger.info(f"QUEUE DETECTED: {ticket}")
            writer.write("queue_detected", {"ticket_repr": repr(ticket)})
            logger.info(f"Entering detect_and_wait with timeout={duration}s")
            writer.write("wait_start", {"timeout_s": duration})

            t0 = time.time()
            final_ticket = await handler.detect_and_wait(timeout=duration)
            elapsed = time.time() - t0

            writer.write("wait_complete", {
                "elapsed_s": elapsed,
                "final_state": final_ticket.state if final_ticket else None,
                "final_likelihood": final_ticket.likelihood if final_ticket else None,
                "ticket_repr": repr(final_ticket),
            })
            logger.info(f"Wait complete: {elapsed:.1f}s → {final_ticket}")

            if final_ticket and final_ticket.state == QueueState.VALID:
                logger.warning("ADMITTED. (Capture stops here — no ATC attempted.)")
                writer.write("admitted_no_action", {})
            elif final_ticket and final_ticket.state == QueueState.EXPIRED:
                logger.warning("Ticket EXPIRED.")
                writer.write("expired", {})
            else:
                logger.warning(f"Wait ended without admission: {final_ticket}")

        # Final snapshot
        try:
            content = await asyncio.wait_for(tab.get_content(), timeout=10.0)
            writer.write("final_page", {
                "url": tab.url,
                "size": len(content) if content else 0,
                "preview": content[:5000] if content else "",
            })
        except Exception:
            pass

        writer.write("session_end", {"capture_records": writer.record_count})
        logger.info(f"Capture complete: {writer.record_count} records to {capture_path}")

    except Exception as e:
        logger.exception("Run failed")
        writer.write("crash", {"error": str(e), "type": type(e).__name__})
    finally:
        writer.close()
        try:
            await browser.stop()
        except Exception:
            pass
        try:
            await forwarder.stop_all()
        except Exception:
            pass


if __name__ == "__main__":
    # Graceful shutdown on Ctrl+C
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")

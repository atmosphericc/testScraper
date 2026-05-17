"""
Walmart virtual queue handler — 2026 ticket-API model.

Rewritten 2026-05-17 after research uncovered Walmart's actual queue
mechanics (not the DOM-overlay-only model the previous version assumed).

Walmart's 2026 queue is its own in-house system (not Queue-it):
  - URL pattern: walmart.com/qp?qpdata=<URL-encoded JSON>
  - Ticket API host: api.waiting-room.walmart.com
  - Endpoints: issueTicket, checkTicket, refreshTicket, validateTickets
  - State machine: pending → valid (admitted) | expired (evicted)
  - Server dictates polling cadence via nextRefreshRelativeTime (20-40s)
  - admissionLikelihood field: "likely" | "unlikely" — Walmart's confidence
    that stock will remain by the time you reach checkout

The queue activates server-side at drop time when traffic on a flagged
SKU crosses threshold. Pokemon TCG drops (Wednesdays ~9 PM ET) always
queue popular items.

Detection approach:
  1. URL contains /qp or /qp?qpdata=... → we've been redirected to queue
  2. CDP Network listener catches responses from api.waiting-room.walmart.com
     → we have a ticket and can read state/likelihood
  3. (Fallback) DOM scan for legacy "Hold my spot" overlay text — older
     drops sometimes used this; modern drops appear to skip it

Pass-through detection:
  - state="valid" from any ticket API response → admitted, ATC available

Bail-out signal:
  - state="expired" → ticket was evicted, must re-enter from fresh session
  - admissionLikelihood="unlikely" repeated → stock probably gone

This module does NOT actively click anything in the 2026 model — Walmart's
own page JS handles polling and admission. We just observe and report.
For the legacy DOM-overlay path we still have join_queue() that clicks
the "Hold my spot" button.

Cookie freshness:
  _px3 has ~60s TTL. The queue page's own JS continuously refreshes _px3
  via PerimeterX challenges while polling. Our keepalive heartbeat
  (homepage nav) must NOT fire on a queueing session because that would
  navigate away from /qp and discard the ticket. The resilient stack's
  pool keepalive needs to know "this session is in queue, skip it".
  (Not yet wired — see TODO at end of file.)
"""

import asyncio
import json
import logging
import re
import time
from typing import Optional, Callable
from urllib.parse import unquote

from .config import QUEUE_POLL_INTERVAL, QUEUE_TIMEOUT

logger = logging.getLogger(__name__)


# ── URL + body signatures for queue detection ────────────────────────────

# Definitive: if we're at /qp the page IS the queue interstitial
_QP_URL_RE = re.compile(r"/qp(\?|/|$)")

# Walmart's queue API host. CDP Network events with these hosts in the
# response URL mean we have a live ticket being polled.
_TICKET_API_HOST = "api.waiting-room.walmart.com"

# Endpoints we care about (subset of: issueTicket, checkTicket, refreshTicket,
# validateTickets). State updates come through any of them.
_TICKET_ENDPOINTS = ("issueTicket", "checkTicket", "refreshTicket")

# Legacy DOM-overlay text patterns. Older drops (pre-2026) showed a
# "Hold my spot and Keep shopping" button in an overlay. Some restocks
# may still use this — we keep detection but no longer assume it.
_LEGACY_OVERLAY_TEXTS = [
    "you're in line",
    "you are in line",
    "your place in line",
    "hold my spot",
    "keep my spot",
    "virtual queue",
]
_LEGACY_OVERLAY_BUTTON_TEXTS = [
    "Hold my spot and Keep shopping",
    "Hold my spot",
    "Keep my spot",
]


# ── data shapes ──────────────────────────────────────────────────────────


class QueueState:
    PENDING = "pending"
    VALID = "valid"          # admitted — ATC available
    EXPIRED = "expired"      # ticket evicted — must re-enter
    UNKNOWN = "unknown"


class AdmissionLikelihood:
    LIKELY = "likely"
    UNLIKELY = "unlikely"
    UNKNOWN = "unknown"


class QueueTicket:
    """Parsed ticket API response or qpdata payload."""

    def __init__(
        self,
        queue_id: Optional[str] = None,
        ticket: Optional[str] = None,
        state: str = QueueState.UNKNOWN,
        likelihood: str = AdmissionLikelihood.UNKNOWN,
        next_refresh_ms: Optional[int] = None,
        expected_turn_unix_ms: Optional[int] = None,
        item_id: Optional[str] = None,
        expires: Optional[int] = None,
        raw: Optional[dict] = None,
    ):
        self.queue_id = queue_id
        self.ticket = ticket
        self.state = state
        self.likelihood = likelihood
        self.next_refresh_ms = next_refresh_ms
        self.expected_turn_unix_ms = expected_turn_unix_ms
        self.item_id = item_id
        self.expires = expires
        self.raw = raw or {}

    def __repr__(self):
        return (
            f"QueueTicket(state={self.state}, likelihood={self.likelihood}, "
            f"queue_id={self.queue_id}, ticket={self.ticket}, "
            f"next_refresh_ms={self.next_refresh_ms}, item_id={self.item_id})"
        )


# ── parsers ──────────────────────────────────────────────────────────────


def parse_qpdata(qpdata_str: str) -> Optional[QueueTicket]:
    """Parse the qpdata URL parameter into a QueueTicket.

    Shape: { queued: true, queue: "<id>", url: "<ticket-api-url>",
             customMetadata: { item: {...} } }
    No ticket/state in qpdata itself — those come from checkTicket later.
    """
    try:
        data = json.loads(unquote(qpdata_str))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    meta_item = (data.get("customMetadata") or {}).get("item") or {}
    return QueueTicket(
        queue_id=data.get("queue"),
        state=QueueState.PENDING,
        likelihood=AdmissionLikelihood.UNKNOWN,
        item_id=str(meta_item.get("itemID") or "") or None,
        raw=data,
    )


def parse_ticket_response(body: dict) -> Optional[QueueTicket]:
    """Parse a checkTicket/issueTicket/refreshTicket response body.

    Shape (from research, walmart-queue-tracker GitHub):
      {
        queue: "qa484c0ebd7014",
        ticket: "<numeric>",
        state: "pending" | "valid" | "expired",
        expectedTurnTimeUnixTimestamp: <ms>,
        customMetadata: {
          admissionLikelihood: "likely" | "unlikely",
          ...
        },
        nextRefreshRelativeTime: <ms>,
        itemId: "<id>",
        expires: <ts>,
      }
    """
    if not isinstance(body, dict):
        return None
    custom = body.get("customMetadata") or {}
    state_raw = (body.get("state") or "").lower()
    state = state_raw if state_raw in (
        QueueState.PENDING, QueueState.VALID, QueueState.EXPIRED,
    ) else QueueState.UNKNOWN
    likelihood_raw = (custom.get("admissionLikelihood") or "").lower()
    likelihood = likelihood_raw if likelihood_raw in (
        AdmissionLikelihood.LIKELY, AdmissionLikelihood.UNLIKELY,
    ) else AdmissionLikelihood.UNKNOWN
    return QueueTicket(
        queue_id=body.get("queue"),
        ticket=str(body.get("ticket")) if body.get("ticket") is not None else None,
        state=state,
        likelihood=likelihood,
        next_refresh_ms=body.get("nextRefreshRelativeTime"),
        expected_turn_unix_ms=body.get("expectedTurnTimeUnixTimestamp"),
        item_id=str(body.get("itemId")) if body.get("itemId") is not None else None,
        expires=body.get("expires"),
        raw=body,
    )


def is_queue_url(url: str) -> bool:
    """True if URL is the queue interstitial (/qp path)."""
    if not url:
        return False
    return bool(_QP_URL_RE.search(url))


def is_ticket_api_url(url: str) -> bool:
    """True if URL is a ticket API endpoint."""
    if not url:
        return False
    if _TICKET_API_HOST not in url:
        return False
    return any(ep in url for ep in _TICKET_ENDPOINTS)


def extract_qpdata_from_url(url: str) -> Optional[str]:
    """Pull the qpdata parameter from a queue URL, if present."""
    if not url:
        return None
    m = re.search(r"[?&]qpdata=([^&]+)", url)
    return m.group(1) if m else None


# ── handler ──────────────────────────────────────────────────────────────


class QueueHandler:
    """Detects and waits through Walmart's virtual queue.

    Usage during a purchase flow:
        handler = QueueHandler(page, status_callback=_status)
        ticket = await handler.detect_and_wait(timeout=QUEUE_TIMEOUT)
        if ticket and ticket.state == QueueState.VALID:
            # admitted — caller proceeds to ATC
        elif ticket and ticket.state == QueueState.EXPIRED:
            # evicted — caller should re-enter from fresh session
        else:
            # timeout or unknown — caller decides next action

    Detection sources (any one is sufficient):
      1. page URL contains /qp
      2. CDP Network response from api.waiting-room.walmart.com
      3. Legacy: page body contains 'you're in line' text + 'Hold my spot' button

    Pass-through detection (any one):
      1. Ticket API response with state="valid"
      2. URL navigates AWAY from /qp (queue page redirects on admission)
      3. Legacy: ATC button becomes active on the original /ip/ page
    """

    def __init__(
        self,
        page,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self._page = page
        self._status_cb = status_callback or (lambda msg: None)
        self._last_ticket: Optional[QueueTicket] = None
        self._unlikely_streak = 0
        self._cdp_handler_attached = False

    # ── public API ───────────────────────────────────────────────────────

    async def detect(self) -> Optional[QueueTicket]:
        """Return a QueueTicket if we're currently in a queue, else None.

        Checks URL first (cheapest), then body for legacy overlay.
        Does NOT poll the ticket API — that's done by the page's own JS.
        We just inspect what's there.
        """
        try:
            url = self._page.url or ""
        except Exception:
            url = ""

        if is_queue_url(url):
            qp = extract_qpdata_from_url(url)
            if qp:
                ticket = parse_qpdata(qp)
                if ticket:
                    logger.info("[QUEUE] Detected queue via /qp URL: %s", ticket)
                    return ticket
            # /qp URL but no qpdata — still a queue page, just less info
            logger.info("[QUEUE] Detected /qp URL without qpdata")
            return QueueTicket(state=QueueState.PENDING)

        # Legacy overlay detection — older drop UIs may still use this
        if await self._has_legacy_overlay():
            logger.info("[QUEUE] Detected legacy overlay (Hold-my-spot button or text)")
            return QueueTicket(state=QueueState.PENDING)

        return None

    async def detect_and_wait(
        self,
        timeout: float = QUEUE_TIMEOUT,
        max_unlikely_streak: int = 3,
    ) -> Optional[QueueTicket]:
        """Wait until admitted, evicted, or timeout.

        Returns the latest QueueTicket with state set, or None on hard
        timeout with no signal at all.

        max_unlikely_streak: if admissionLikelihood='unlikely' is observed
        this many times in a row, return early with the latest ticket so
        the caller can decide whether to bail. This avoids waiting hours
        for an item that's effectively gone.
        """
        # Initial detection
        ticket = await self.detect()
        if ticket is None:
            return None

        # If already admitted on first check (rare but possible), return
        if ticket.state == QueueState.VALID:
            return ticket

        # Attach CDP listener for ticket API responses if we haven't yet
        await self._maybe_attach_cdp_listener()

        # Legacy overlay path: click "Hold my spot" if it's there
        await self._try_legacy_join()

        deadline = time.monotonic() + timeout
        self._status_cb("[QUEUE] In queue — waiting for admission...")
        logger.info("[QUEUE] Starting wait loop (timeout=%.0fs)", timeout)

        check_interval = 1.0   # poll the page state every second
        while time.monotonic() < deadline:
            # Check if we have a fresh ticket from CDP
            if self._last_ticket is not None:
                t = self._last_ticket

                if t.state == QueueState.VALID:
                    self._status_cb("[QUEUE] Admitted — ticket state=valid")
                    logger.info("[QUEUE] Admitted: %s", t)
                    return t

                if t.state == QueueState.EXPIRED:
                    self._status_cb("[QUEUE] Ticket EXPIRED — must re-enter")
                    logger.warning("[QUEUE] Ticket expired: %s", t)
                    return t

                # Track unlikely streak for early bail
                if t.likelihood == AdmissionLikelihood.UNLIKELY:
                    self._unlikely_streak += 1
                    if self._unlikely_streak >= max_unlikely_streak:
                        self._status_cb(
                            f"[QUEUE] Bailing — admissionLikelihood=unlikely "
                            f"x{self._unlikely_streak}"
                        )
                        logger.warning(
                            "[QUEUE] Early bail on unlikely streak=%d: %s",
                            self._unlikely_streak, t,
                        )
                        return t
                else:
                    # Reset streak on any non-unlikely observation
                    self._unlikely_streak = 0

            # Also check URL — sometimes Walmart's queue JS navigates the
            # tab off /qp on admission without us seeing a state="valid"
            # ticket response (timing race)
            try:
                url = self._page.url or ""
            except Exception:
                url = ""
            if url and not is_queue_url(url):
                # Navigated away from /qp — likely admitted
                self._status_cb("[QUEUE] Page navigated away from /qp — admitted")
                logger.info("[QUEUE] URL navigated to %s, treating as admitted", url[:80])
                return QueueTicket(state=QueueState.VALID)

            await asyncio.sleep(check_interval)

        # Hit timeout
        self._status_cb(f"[QUEUE] Timed out after {timeout:.0f}s")
        logger.warning("[QUEUE] Timeout — last ticket: %s", self._last_ticket)
        return self._last_ticket   # may be None or a stale pending ticket

    # ── CDP listener for ticket API responses ────────────────────────────

    async def _maybe_attach_cdp_listener(self):
        """Attach a one-shot CDP listener that captures responses from
        api.waiting-room.walmart.com and parses them into self._last_ticket.

        Idempotent — only attaches once per QueueHandler instance.

        zendriver-specific API: page.add_handler(network event handler).
        If the underlying API differs we fall back gracefully — the URL
        check and legacy overlay detection still work without it.
        """
        if self._cdp_handler_attached:
            return
        try:
            from zendriver import cdp as _cdp

            async def _on_response_received(event):
                """Catch Network.responseReceived for ticket API URLs."""
                try:
                    resp = getattr(event, "response", None)
                    if resp is None:
                        return
                    url = getattr(resp, "url", "") or ""
                    if not is_ticket_api_url(url):
                        return
                    request_id = getattr(event, "request_id", None)
                    if not request_id:
                        return
                    # Pull the body via Network.getResponseBody
                    try:
                        body_event = await self._page.send(
                            _cdp.network.get_response_body(request_id)
                        )
                    except Exception as e:
                        logger.debug("[QUEUE] getResponseBody failed: %s", e)
                        return
                    # zendriver returns (body, base64encoded) tuple
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
                    parsed = parse_ticket_response(body_json)
                    if parsed is None:
                        return
                    self._last_ticket = parsed
                    logger.info(
                        "[QUEUE] CDP captured ticket: %s",
                        parsed,
                    )
                except Exception as e:
                    logger.debug("[QUEUE] CDP handler raised: %s", e)

            # zendriver's tab.add_handler API
            try:
                self._page.add_handler(
                    _cdp.network.ResponseReceived, _on_response_received,
                )
                self._cdp_handler_attached = True
                logger.debug("[QUEUE] CDP Network listener attached")
            except AttributeError:
                # Older/newer zendriver may use different API — fail soft
                logger.info(
                    "[QUEUE] zendriver Network handler unavailable — "
                    "falling back to URL/DOM detection only"
                )
        except ImportError:
            logger.debug("[QUEUE] zendriver.cdp not available — URL/DOM detection only")

    # ── legacy overlay support ───────────────────────────────────────────

    async def _has_legacy_overlay(self) -> bool:
        """Check page body for legacy 'you're in line' text or Hold-my-spot button."""
        try:
            body_text = await self._page.evaluate("document.body.innerText")
        except Exception:
            return False
        if not body_text:
            return False
        body_lower = body_text.lower()
        return any(sig in body_lower for sig in _LEGACY_OVERLAY_TEXTS)

    async def _try_legacy_join(self) -> bool:
        """If a legacy 'Hold my spot' button is visible, click it.

        Returns True if a click was issued. The 2026 queue doesn't usually
        present this button — it just redirects to /qp — but older drop
        UIs sometimes still do.
        """
        for text in _LEGACY_OVERLAY_BUTTON_TEXTS:
            try:
                els = await self._page.xpath(f'//button[contains(., "{text}")]')
                if not els:
                    continue
                btn = els[0]
                try:
                    visible = await btn.apply("(e) => !!(e.offsetWidth || e.offsetHeight)")
                except Exception:
                    visible = True
                if not visible:
                    continue
                try:
                    await btn.scroll_into_view()
                except Exception:
                    pass
                try:
                    await btn.click()
                    logger.info("[QUEUE] Clicked legacy '%s' button", text)
                    return True
                except Exception as e:
                    logger.debug("[QUEUE] Legacy click failed: %s", e)
            except Exception:
                continue
        return False


# ── TODO: cooperation with the resilient-stack keepalive ─────────────────
#
# When a session enters a queue, its tab is parked on /qp. The pool's
# keepalive loop would normally re-navigate the tab to the homepage every
# (max_age * N) seconds — that would discard the queue ticket.
#
# Fix path: SessionEntry needs a "do not keepalive" flag. The purchase
# manager sets it on the chosen checkout-bound session before queue entry
# and clears it on admission or abandonment. The pool's _heartbeat_one
# skips sessions with the flag set.
#
# Not implemented yet because the resilient stack itself hasn't been
# wired to the purchase flow (Phase 2 cutover hasn't happened). Add this
# when Phase 2 lands.

"""
Walmart checkout-capture CDP logger — temporary tool for hybrid-API research.

Enable: ``WALMART_CAPTURE_CHECKOUT=1 CHECKOUT_MODE=TEST python -m walmart.walmart_app``

When enabled, attaches RequestWillBeSent + LoadingFinished + ResponseReceived
handlers to Tab 2 (the checkout tab) and writes every checkout-related HTTP
call to ``walmart/logs/checkout_capture_<ts>.jsonl`` — one JSON object per
request/response cycle, containing URL, method, request headers, request body,
response status, response headers, response body, and timing.

URL filter is conservative — only paths that matter for the hybrid:
  - ``/api/v3/cart/`` (ATC)
  - ``/api/checkout/v3/`` (contract, fulfillment, address, payment, order)
  - ``/api/checkout-customer/`` (PIE card submission)
  - ``securedataweb.walmart.com`` (PIE key fetch)
  - ``/orchestra/`` (GraphQL — already captured by stock monitor but useful
                    to correlate)

Delete this file (and the wire call in session_manager._open_checkout_tab)
once the hybrid is implemented.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from zendriver import cdp


logger = logging.getLogger("walmart.checkout_capture")

_URL_FILTERS = (
    "/api/v3/cart/",
    "/api/checkout/v3/",
    "/api/checkout-customer/",
    "securedataweb.walmart.com",
    "/orchestra/",
)


def is_enabled() -> bool:
    return os.environ.get("WALMART_CAPTURE_CHECKOUT", "").lower() in ("1", "true", "yes", "on")


class CheckoutCapture:
    """Hooks Tab 2 CDP network events and dumps to JSONL.

    Lifecycle:
      capture = CheckoutCapture(checkout_page)
      await capture.attach()         # called from session_manager._open_checkout_tab
      ...                             # purchase runs
      await capture.detach()          # called from session_manager.stop()
    """

    def __init__(self, page):
        self._page = page
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path("walmart") / "logs"
        out_dir.mkdir(parents=True, exist_ok=True)
        self._path = out_dir / f"checkout_capture_{ts}.jsonl"
        self._attached = False
        # CDP handlers fire on zendriver's internal thread pool, not the main
        # asyncio loop. Coroutines must be scheduled via run_coroutine_threadsafe
        # with an explicit loop reference. Captured at attach time (when we know
        # the loop is the one we want to dispatch onto).
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def path(self) -> Path:
        return self._path

    async def attach(self):
        if self._attached:
            return
        try:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.get_event_loop()
            await self._page.send(cdp.network.enable())
            self._page.add_handler(cdp.network.RequestWillBeSent, self._on_request)
            self._page.add_handler(cdp.network.ResponseReceived, self._on_response)
            self._page.add_handler(cdp.network.LoadingFinished, self._on_loading_finished)
            self._attached = True
            logger.warning(
                "[CAPTURE] Checkout capture ENABLED — writing to %s "
                "(disable by unsetting WALMART_CAPTURE_CHECKOUT)",
                self._path,
            )
            self._append({"event": "attached", "ts": time.time(), "path": str(self._path)})
        except Exception as e:
            logger.warning("[CAPTURE] Failed to attach handlers: %s", e)

    async def detach(self):
        if not self._attached:
            return
        try:
            self._append({"event": "detached", "ts": time.time()})
        except Exception:
            pass
        self._attached = False

    def _matches(self, url: str) -> bool:
        if not url:
            return False
        for needle in _URL_FILTERS:
            if needle in url:
                return True
        return False

    def _append(self, record: dict):
        try:
            with self._lock:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.debug("[CAPTURE] Append failed: %s", e)

    def _on_request(self, event):
        try:
            req = event.request
            url = getattr(req, "url", "")
            if not self._matches(url):
                return
            request_id = str(event.request_id)
            method = getattr(req, "method", "")
            headers = dict(getattr(req, "headers", {}) or {})
            post_data = getattr(req, "post_data", None)
            has_post_data = bool(getattr(req, "has_post_data", False) or post_data)
            initiator = getattr(event, "initiator", None)
            initiator_type = getattr(initiator, "type_", None) if initiator else None
            entry = {
                "event": "request",
                "ts": time.time(),
                "request_id": request_id,
                "url": url,
                "method": method,
                "headers": headers,
                "post_data": post_data,
                "post_data_complete": post_data is not None,
                "has_post_data_flag": has_post_data,
                "initiator_type": str(initiator_type) if initiator_type else None,
                "document_url": getattr(event, "document_url", None),
            }
            with self._lock:
                self._pending[request_id] = {
                    "url": url,
                    "method": method,
                    "started": time.time(),
                    "post_data_missing": (post_data is None and has_post_data),
                }
            self._append(entry)
            # Gap 1: cookies are redacted from request.headers by Chromium.
            # Snapshot the live Tab 2 cookie jar at the moment this request
            # fired so we can correlate _px3 / _abck / QueueITAccepted state
            # against the request. Scheduled on the loop so the sync handler
            # doesn't block.
            self._schedule_cookie_snapshot(request_id, url)
        except Exception as e:
            logger.debug("[CAPTURE] _on_request error: %s", e)

    def _schedule_cookie_snapshot(self, request_id: str, url: str):
        async def _snap():
            try:
                cookies = await self._page.send(cdp.network.get_all_cookies())
                jar = []
                # cookies is iterable of cdp.network.Cookie objects
                for c in (cookies or []):
                    try:
                        jar.append({
                            "name": getattr(c, "name", None),
                            "value": getattr(c, "value", None),
                            "domain": getattr(c, "domain", None),
                            "path": getattr(c, "path", None),
                            "expires": getattr(c, "expires", None),
                            "http_only": getattr(c, "http_only", None),
                            "secure": getattr(c, "secure", None),
                            "session": getattr(c, "session", None),
                        })
                    except Exception:
                        continue
                self._append({
                    "event": "cookies_at_request",
                    "ts": time.time(),
                    "request_id": request_id,
                    "url": url,
                    "cookie_count": len(jar),
                    "cookies": jar,
                })
            except Exception as e:
                self._append({
                    "event": "cookies_snapshot_error",
                    "ts": time.time(),
                    "request_id": request_id,
                    "url": url,
                    "error": str(e),
                })

        # CDP handlers fire on zendriver's worker thread — schedule on the
        # captured asyncio loop with run_coroutine_threadsafe (asyncio.ensure_future
        # silently no-ops when called from a non-loop thread).
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(_snap(), self._loop)
            except RuntimeError:
                pass

    def _on_response(self, event):
        try:
            resp = event.response
            url = getattr(resp, "url", "")
            if not self._matches(url):
                return
            request_id = str(event.request_id)
            entry = {
                "event": "response_meta",
                "ts": time.time(),
                "request_id": request_id,
                "url": url,
                "status": getattr(resp, "status", None),
                "status_text": getattr(resp, "status_text", None),
                "headers": dict(getattr(resp, "headers", {}) or {}),
                "mime_type": getattr(resp, "mime_type", None),
            }
            self._append(entry)
        except Exception as e:
            logger.debug("[CAPTURE] _on_response error: %s", e)

    def _on_loading_finished(self, event):
        request_id = str(getattr(event, "request_id", ""))
        if not request_id:
            return
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if not pending or not self._matches(pending.get("url", "")):
            return

        async def _fetch():
            # Gap 2: if RequestWillBeSent had no post_data (Chromium truncates
            # large bodies or skips multipart), pull it now via
            # Network.getRequestPostData. Only attempt for requests that were
            # flagged as missing-body at request-time.
            if pending.get("post_data_missing"):
                try:
                    result = await self._page.send(
                        cdp.network.get_request_post_data(request_id=event.request_id)
                    )
                    post_body = result if isinstance(result, str) else (
                        result[0] if isinstance(result, tuple) else None
                    )
                    if isinstance(post_body, (bytes, bytearray)):
                        try:
                            post_body = post_body.decode("utf-8", errors="replace")
                        except Exception:
                            post_body = repr(post_body)
                    self._append({
                        "event": "request_post_data",
                        "ts": time.time(),
                        "request_id": request_id,
                        "url": pending["url"],
                        "method": pending["method"],
                        "body": post_body if post_body and len(post_body) < 200_000
                                else (post_body[:200_000] + "…<truncated>") if post_body else None,
                        "body_truncated": bool(post_body and len(post_body) >= 200_000),
                    })
                except Exception as e:
                    self._append({
                        "event": "request_post_data_error",
                        "ts": time.time(),
                        "request_id": request_id,
                        "url": pending["url"],
                        "error": str(e),
                    })

            try:
                result = await self._page.send(
                    cdp.network.get_response_body(request_id=event.request_id)
                )
                body = None
                base64_encoded = False
                if isinstance(result, tuple):
                    body, base64_encoded = result[0], result[1]
                else:
                    body = result
                if isinstance(body, (bytes, bytearray)):
                    try:
                        body = body.decode("utf-8", errors="replace")
                    except Exception:
                        body = repr(body)
                self._append({
                    "event": "response_body",
                    "ts": time.time(),
                    "request_id": request_id,
                    "url": pending["url"],
                    "method": pending["method"],
                    "duration_ms": int((time.time() - pending["started"]) * 1000),
                    "base64_encoded": bool(base64_encoded),
                    "body": body if body and len(body) < 200_000 else (body[:200_000] + "…<truncated>") if body else None,
                    "body_truncated": bool(body and len(body) >= 200_000),
                })
            except Exception as e:
                self._append({
                    "event": "response_body_error",
                    "ts": time.time(),
                    "request_id": request_id,
                    "url": pending["url"],
                    "error": str(e),
                })

        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(_fetch(), self._loop)
            except RuntimeError:
                pass

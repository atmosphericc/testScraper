"""
walmart/http_stock_checker.py — raw-HTTP (curl_cffi) Walmart stock checker.

Phase 1 of docs/WALMART_FAST_MONITOR.md — the lightweight transport that
replaces full browser page-loads for MONITORING. It fetches `/ip/<item_id>`
with a Chrome-impersonating TLS client and parses `__NEXT_DATA__`, producing
the SAME in_stock / QUEUED / BLOCKED classification as the browser adapter
(parsing is delegated to `WalmartAdapter._extract_product`).

Why it exists (see the doc + the 2026-08-19 drop 0-for post-mortem):
  - A raw HTTP request is 5-10x lighter than rendering a page, so each IP
    sustains a far higher check rate before PerimeterX scores it.
  - It is ANONYMOUS: it needs only a `_px3` device-clearance cookie (minted
    per-IP by the harvester, Phase 2), NOT a logged-in account. So hammering
    it — even bursting at the drop second — never touches the buyer's account.
    The logged-in buyer runs separately on the clean home IP.

⚠ curl_cffi is BANNED from the Target/Shape request path (see CLAUDE.md). This
  module is WALMART-ONLY. Do not import it into any Target code.

Status: transport scaffold. Rate control, per-IP `_px3` harvesting, and the
resilient-stack wiring are later phases. This module is a pure function of
(item_id, proxy, px3) → result, so it is unit-testable offline and safe to
smoke-test with a single anonymous request.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .walmart_adapter import WalmartAdapter

# One adapter instance reused for its parse helpers — keeps classification
# identical to the browser transport (same availabilityStatus/showAtc/seller
# rules). Cheap to construct; holds no per-request state.
_ADAPTER = WalmartAdapter()

# Chrome-impersonation target for curl_cffi's TLS/JA3 + HTTP2 fingerprint.
# Overridable via check_item(impersonate=...) if Walmart's edge starts
# flagging a specific build. "chrome124" is a recent, widely-accepted profile.
DEFAULT_IMPERSONATE = "chrome124"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
_QPDATA_RE = re.compile(r"qpdata=([^&]+)")
# Body-level block/challenge signatures (mirror adapter.is_preflight_clean).
_BLOCK_SIGNATURES = ("robot or human", "px-captcha", "/blocked")
# Body-level queue signatures (mirror the adapter's shape-B detection).
_QUEUE_SIGNATURES = (
    "api.waiting-room.walmart.com",
    "issueTicket",
    "checkTicket",
)

# Request headers mirror the browser fetch in walmart_adapter._FETCH_JS_TEMPLATE.
_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.walmart.com/",
    "Cache-Control": "max-age=0",
}


@dataclass
class HttpCheckResult:
    """One item's stock-check outcome — the raw-HTTP analog of the adapter's
    per-item parse. `ok` means the fetch + parse succeeded (item is either in
    or out of stock); `blocked`/`queued`/`error` are the non-ok terminals."""
    item_id: str
    ok: bool = False
    in_stock: bool = False
    availability: str = "UNKNOWN"
    queued: bool = False
    queue_info: Optional[dict] = None
    blocked: bool = False
    http_status: int = 0
    price: Optional[float] = None
    name: Optional[str] = None
    error: Optional[str] = None
    ms: float = 0.0
    final_url: str = ""

    def summary(self) -> str:
        if self.blocked:
            return f"{self.item_id} BLOCKED (http={self.http_status})"
        if self.queued:
            return f"{self.item_id} QUEUED"
        if self.error:
            return f"{self.item_id} ERROR {self.error}"
        state = "IN_STOCK" if self.in_stock else "OOS"
        price = f"${self.price}" if self.price is not None else "$?"
        return f"{self.item_id} {state} [{self.availability}] {price} {self.name or ''}".strip()


def _extract_availability(item_id: str, html: str) -> HttpCheckResult:
    """Parse __NEXT_DATA__ out of the PDP HTML and classify availability,
    reusing WalmartAdapter._extract_product so the verdict matches the
    browser transport exactly. Caller has already ruled out block/queue."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return HttpCheckResult(item_id=item_id, error="NO_NEXT_DATA")
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return HttpCheckResult(item_id=item_id, error="NEXT_DATA_NOT_JSON")
    product = (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialData", {})
        .get("data", {})
        .get("product")
    )
    if not isinstance(product, dict):
        return HttpCheckResult(item_id=item_id, error="NO_PRODUCT")
    # The adapter's _extract_product keys off usItemId == item_id (the browser
    # JS backfills it when Walmart omits it); do the same before delegating.
    if not product.get("usItemId"):
        product["usItemId"] = item_id
    parsed = _ADAPTER._extract_product(item_id, product)
    if parsed is None:
        return HttpCheckResult(item_id=item_id, error="EXTRACT_MISMATCH")
    return HttpCheckResult(
        item_id=item_id,
        ok=True,
        in_stock=parsed["in_stock"],
        availability=parsed["availability"],
        price=parsed["price"],
        name=parsed["name"],
    )


def check_item(
    item_id: str,
    *,
    proxy: Optional[str] = None,
    px3: Optional[str] = None,
    cookies: Optional[dict] = None,
    impersonate: str = DEFAULT_IMPERSONATE,
    timeout: float = 10.0,
    session=None,
) -> HttpCheckResult:
    """Fetch and classify one item's stock via curl_cffi.

    proxy:   full upstream proxy URL (e.g. the BD `http://brd-...@host:port`);
             curl_cffi does the authenticated CONNECT itself — no local
             forwarder needed (unlike the zendriver path).
    px3:     the `_px3` clearance cookie minted for THIS proxy's IP (Phase 2).
             Anonymous (no account cookies) by design.
    cookies: extra cookies to merge (e.g. ACID/locale) if needed.
    session: an optional curl_cffi Session for connection reuse across calls.
    """
    from curl_cffi import requests as cffi

    url = f"https://www.walmart.com/ip/{item_id}"
    jar = {}
    if px3:
        jar["_px3"] = px3
    if cookies:
        jar.update(cookies)

    proxies = {"http": proxy, "https": proxy} if proxy else None
    getter = session.get if session is not None else cffi.get

    t0 = time.monotonic()
    try:
        resp = getter(
            url,
            impersonate=impersonate,
            headers=_BASE_HEADERS,
            cookies=jar or None,
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as e:  # network / proxy / TLS error
        return HttpCheckResult(
            item_id=item_id, error=f"{type(e).__name__}:{str(e)[:60]}",
            ms=(time.monotonic() - t0) * 1000,
        )

    ms = (time.monotonic() - t0) * 1000
    final_url = str(getattr(resp, "url", "") or "")
    status = getattr(resp, "status_code", 0)

    # ── block detection (URL redirect to /blocked) ───────────────────────
    if "/blocked" in final_url:
        return HttpCheckResult(item_id=item_id, blocked=True, http_status=status,
                               ms=ms, final_url=final_url)

    # ── queue detection, shape A: redirect to /qp?qpdata=... ─────────────
    if "/qp" in final_url or "qpdata=" in final_url:
        qinfo = None
        qm = _QPDATA_RE.search(final_url)
        if qm:
            try:
                from urllib.parse import unquote
                qinfo = json.loads(unquote(qm.group(1)))
            except Exception:
                qinfo = None
        return HttpCheckResult(item_id=item_id, queued=True, queue_info=qinfo,
                               http_status=status, ms=ms, final_url=final_url)

    try:
        body = resp.text or ""
    except Exception:
        body = ""
    body_lower = body.lower()

    # ── block detection (body challenge signature) ───────────────────────
    if any(sig in body_lower for sig in _BLOCK_SIGNATURES):
        return HttpCheckResult(item_id=item_id, blocked=True, http_status=status,
                               ms=ms, final_url=final_url)

    # ── queue detection, shape B: waiting-room signature in body ─────────
    if any(sig in body for sig in _QUEUE_SIGNATURES):
        return HttpCheckResult(item_id=item_id, queued=True, http_status=status,
                               ms=ms, final_url=final_url)

    if status >= 400:
        return HttpCheckResult(item_id=item_id, error=f"HTTP_{status}",
                               http_status=status, ms=ms, final_url=final_url)

    # ── availability parse ───────────────────────────────────────────────
    result = _extract_availability(item_id, body)
    result.http_status = status
    result.ms = ms
    result.final_url = final_url
    return result


def check_items(
    item_ids: list[str],
    *,
    proxy: Optional[str] = None,
    px3: Optional[str] = None,
    cookies: Optional[dict] = None,
    impersonate: str = DEFAULT_IMPERSONATE,
    timeout: float = 10.0,
    reuse_session: bool = True,
) -> dict[str, HttpCheckResult]:
    """Check several items over one connection (sequential). Concurrency and
    rate control belong to the monitor loop (Phase 3); this is the primitive."""
    from curl_cffi import requests as cffi
    sess = cffi.Session() if reuse_session else None
    try:
        return {
            iid: check_item(iid, proxy=proxy, px3=px3, cookies=cookies,
                            impersonate=impersonate, timeout=timeout, session=sess)
            for iid in item_ids
        }
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass


# ── standalone smoke test ────────────────────────────────────────────────
# `python -m walmart.http_stock_checker <item_id> [proxy_url]`
# One anonymous request (no account). Validates that curl_cffi's Chrome
# impersonation gets through Walmart's edge and the parse works end-to-end.
if __name__ == "__main__":
    import sys

    item = sys.argv[1] if len(sys.argv) > 1 else "20497167347"
    prox = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"HTTP stock check (anonymous): item={item} proxy={'yes' if prox else 'home-IP'}")
    r = check_item(item, proxy=prox)
    print("  ", r.summary())
    print(f"   http={r.http_status} ms={r.ms:.0f} blocked={r.blocked} "
          f"queued={r.queued} ok={r.ok} err={r.error}")
    print(f"   final_url={r.final_url[:90]}")

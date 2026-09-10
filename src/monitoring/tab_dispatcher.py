"""
Browser-native bulk stock dispatcher.

Picks an idle SessionEntry (one Chrome on one BD ISP IP) and runs a bulk
product_summary_with_fulfillment_v1 fetch via its long-lived target.com tab.

Inherits the tab's:
  - JA3/JA4 (real Chrome, indistinguishable from a human's browser)
  - Live PX/Akamai cookies (continuously refreshed by the keepalive loop)
  - Real visitor_id (from the session's actual Target visit, not a guess)
  - Behavioral context (the tab has navigated, scrolled-on-load, accumulated
    pageviews — full session trust)

All curl_cffi is out of the request path. Preflight is the only curl_cffi
survivor in this stack.
"""

from __future__ import annotations

import asyncio
import logging
import random
import os
import time
from dataclasses import dataclass
from typing import Optional

from src.session.multi_session_pool import (
    MultiSessionPool,
    SessionEntry,
    CONSECUTIVE_ERROR_RECYCLE_THRESHOLD,
)


logger = logging.getLogger(__name__)

REDSKY_BULK = (
    "https://redsky.target.com/redsky_aggregations/v1/web/"
    "product_summary_with_fulfillment_v1"
)
REDSKY_API_KEYS = [
    "ff457966e64d5e877fdbad070f276d18ecec4a01",
    "9f36aeafbe60771e321a7cc95a78140772ab3e96",
]
DEFAULT_STORE_ID = "865"
DEFAULT_TAB_EVAL_TIMEOUT_S = 15.0
PDP_DWELL_S_MIN = 4.0
PDP_DWELL_S_MAX = 6.0
PDP_NAV_TIMEOUT_S = 20.0
# Hard cap from product_summary_with_fulfillment_v1: 30 TCINs per request.
# Chunk at 28 to leave a small safety margin for any future tightening.
MAX_TCINS_PER_REQUEST = 28


@dataclass
class BulkResult:
    session_id: str
    pinned_ip: str
    http_status: int            # 0 for exception, 2xx/4xx/5xx for HTTP
    latency_ms: int
    raw: Optional[dict] = None  # parsed JSON body when http_status==200
    error: Optional[str] = None


class TabDispatcher:
    """Fires bulk RedSky requests through random Chromes from a MultiSessionPool.
    Each Chrome's busy_lock serializes its own dispatches; concurrent sweeps go
    to different Chromes."""

    def __init__(
        self,
        session_pool: MultiSessionPool,
        tcins: list[str],
        store_id: str = DEFAULT_STORE_ID,
        tab_eval_timeout_s: float = DEFAULT_TAB_EVAL_TIMEOUT_S,
        max_tcins_per_request: int = MAX_TCINS_PER_REQUEST,
    ):
        self.session_pool = session_pool
        self.tcins = list(tcins)
        self.store_id = store_id
        self.tab_eval_timeout_s = tab_eval_timeout_s
        self.max_tcins_per_request = max(1, int(max_tcins_per_request))
        self._chunks: list[list[str]] = self._build_balanced_chunks(
            self.tcins, self.max_tcins_per_request)
        self._next_chunk_idx = 0

    @staticmethod
    def _build_balanced_chunks(tcins: list[str], max_size: int) -> list[list[str]]:
        """Split tcins into the fewest chunks of size <= max_size, sized as
        evenly as possible. With 33 tcins and max=28: 2 chunks of 17 and 16
        (uniform refresh rate), not [28, 5] (uneven refresh)."""
        if not tcins:
            return [[]]
        n_chunks = max(1, (len(tcins) + max_size - 1) // max_size)
        base, extra = divmod(len(tcins), n_chunks)
        chunks: list[list[str]] = []
        i = 0
        for k in range(n_chunks):
            size = base + (1 if k < extra else 0)
            chunks.append(tcins[i:i + size])
            i += size
        return chunks

    # ───────── primary API: one bulk stock-check sweep ─────────

    async def dispatch_one_sweep(self) -> Optional[BulkResult]:
        """Fire one bulk fetch covering one chunk of TCINs through a random
        ready Chrome. Chunks rotate so every TCIN is refreshed at sweep_rate /
        n_chunks. Returns None if no session is currently available."""
        s = self.session_pool.pick_session()
        if s is None:
            return None
        chunk = self._chunks[self._next_chunk_idx]
        self._next_chunk_idx = (self._next_chunk_idx + 1) % len(self._chunks)
        from src.monitoring import redsky_channel as _rc
        if _rc.is_raw():
            return await self._fire_raw_on(s, chunk)
        return await self._fire_bulk_on(s, chunk)

    async def dispatch_verify(self, tcins: list[str]) -> Optional[BulkResult]:
        """Fire a cache-busted bulk fetch for a small TCIN set through a random
        ready Chrome. Used by the cloaking-alarm verification path: forces an
        origin read so a stale edge-cached OUT_OF_STOCK cannot mask a live
        restock. Returns None if no session is available."""
        if not tcins:
            return None
        s = self.session_pool.pick_session()
        if s is None:
            return None
        from src.monitoring import redsky_channel as _rc
        if _rc.is_raw():
            return await self._fire_raw_on(s, list(tcins), cache_bust=True)
        return await self._fire_bulk_on(s, list(tcins), cache_bust=True)

    # ───────── behavioral mixin: navigate to a PDP, dwell, return ─────────

    async def dispatch_behavioral_pdp(self, target_tcin: str) -> Optional[BulkResult]:
        """Navigate the chosen session's tab to /p/A-{tcin}, dwell briefly,
        let the page settle. Used by the behavioral mixin to make the traffic
        shape look like 'user occasionally browses a product page' rather than
        'pure API polling'. Side effect: refreshes Shape/Akamai cookies on
        the tab via the real PDP load."""
        s = self.session_pool.pick_session()
        if s is None:
            return None
        async with s.busy_lock:
            if s.tab is None or s.state != "ready":
                return BulkResult(s.id, s.proxy_ip, 0, 0,
                                  error=f"session_not_ready:{s.state}")
            s.in_flight = True
            s.last_request_at = time.time()
            t0 = time.time()
            try:
                pdp_url = f"https://www.target.com/p/A-{target_tcin}"
                await asyncio.wait_for(s.tab.get(pdp_url), timeout=PDP_NAV_TIMEOUT_S)
                await asyncio.sleep(random.uniform(PDP_DWELL_S_MIN, PDP_DWELL_S_MAX))
                # Return to homepage so the tab's "current page" stays target.com root
                # (keeps the next fetch's referer plausible without a separate nav)
                try:
                    await asyncio.wait_for(
                        s.tab.get("https://www.target.com/"),
                        timeout=PDP_NAV_TIMEOUT_S,
                    )
                except Exception:
                    pass
                ms = int((time.time() - t0) * 1000)
                s.consecutive_errors = 0
                return BulkResult(s.id, s.proxy_ip, 200, ms, raw=None)
            except Exception as e:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                return BulkResult(s.id, s.proxy_ip, 0,
                                  int((time.time() - t0) * 1000),
                                  error=f"behavioral_pdp:{type(e).__name__}:{e}")
            finally:
                s.in_flight = False

    # ───────── 2026-09-09 raw app-channel read (RESILIENT_REDSKY_CHANNEL=apps_raw) ─────────

    @staticmethod
    def _raw_apps_get(url: str, headers: dict, proxy: str, timeout_s: float):
        """Blocking urllib GET through the session's local forwarder (runs in a
        worker thread). Returns (status, body_text); HTTP errors return their
        status + body so the captcha envelope reaches the park logic."""
        import urllib.error
        import urllib.request
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {}))
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(req, timeout=timeout_s) as r:
                return int(r.status), r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode('utf-8', 'replace')
            except Exception:
                body = ''
            return int(e.code), body

    async def _fire_raw_on(self, s: SessionEntry, tcins: list[str],
                           cache_bust: bool = False) -> BulkResult:
        """Read the mobile-app aggregation as RAW HTTP through this session's
        forwarder (same Bright Data exit as its Chrome). Probe 2026-09-09
        through the walled exit 31.105.228.245: web-in-browser = 403 captcha,
        this path = HTTP 200 product data. The session's Chrome stays parked on
        target.com untouched; park/backoff/park-clear logic is shared with the
        browser path via the identical BulkResult."""
        import json as _json
        from src.monitoring import redsky_channel as _rc
        async with s.busy_lock:
            if s.state != "ready":
                return BulkResult(s.id, s.proxy_ip, 0, 0,
                                  error=f"session_not_ready:{s.state}")
            s.in_flight = True
            s.last_request_at = time.time()
            t0 = time.time()
            # 2026-09-09 research: no evidence RedSky edge-caches this aggregation, and a
            # per-request unique query param is a client-shape uniqueness signal to
            # HUMAN. The 293/293 + soak reads used no cache-bust. Off unless
            # RESILIENT_RAW_CACHE_BUST=1 (the in-page web verify keeps its bust).
            _bust = bool(cache_bust) and os.environ.get('RESILIENT_RAW_CACHE_BUST', '0') == '1'
            url = _rc.raw_apps_url(tcins, self.store_id, cache_bust=_bust)
            # RESILIENT_HARVEST_VIA_LOCAL_IP=1 launches the pool without forwarders:
            # read direct from the home IP then, exactly like that mode's Chromes.
            proxy = (None if getattr(self.session_pool, 'harvest_via_local_ip', False)
                     else f"http://127.0.0.1:{s.local_port}")
            try:
                status, text = await asyncio.wait_for(
                    asyncio.to_thread(self._raw_apps_get, url, _rc.raw_headers(), proxy,
                                      self.tab_eval_timeout_s),
                    timeout=self.tab_eval_timeout_s + 2.0,
                )
                ms = int((time.time() - t0) * 1000)
                body = None
                try:
                    body = _json.loads(text)
                except Exception:
                    body = None
                res = self._interpret_eval_result(
                    s, {"__http_status": status, "__body": body, "__body_text": (text or "")[:800]}, ms)
                if status == 200:
                    s.rate_parks = 0                      # a clean read resets the ladder
                elif status == 404 and '"Not Found"' in (text or ''):
                    # App-channel per-IP rate limiter (soaks 2026-09-09): 0.5 reads/s
                    # = 293/293 clean for 10 min; 2 reads/s = HTTP 404
                    # {"errors":[{"message":"Not Found"}],"data":{}} on every read after
                    # ~166 reads (~80 s), still 404 >3 min after the burst stopped.
                    # Park THIS session (RESILIENT_RAW_404_PARK_S base, x2 per repeat,
                    # cap x8; a 200 resets) and do not let the 404 sour its /16
                    # (pick_session excludes a /16 after 2 recent 4xx, which would
                    # drain 13 of 16 exits at once for a per-IP limit).
                    try:
                        _base = max(0.0, float(os.environ.get('RESILIENT_RAW_404_PARK_S', '120')))
                    except (TypeError, ValueError):
                        _base = 120.0
                    if s.recent_4xx:
                        s.recent_4xx.pop()
                    s.consecutive_errors = max(0, s.consecutive_errors - 1)   # not a crash signal
                    if _base > 0:
                        s.rate_parks = getattr(s, 'rate_parks', 0) + 1
                        _park = _base * min(8.0, 2.0 ** (s.rate_parks - 1))
                        s.rate_parked_until = time.time() + _park
                        logger.warning(f"[STOCK][RAW-RATE] {s.id} ({s.proxy_ip}) app-channel 404 "
                                       f"(per-IP rate limit) — parked {_park:.0f}s (park #{s.rate_parks})")
                    res.error = f"raw_404_rate_limited:{(text or '')[:120]}"
                return res
            except asyncio.TimeoutError:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                    logger.warning(f"[DISPATCHER] {s.id} flagged crashed after "
                                   f"{s.consecutive_errors} consec errors (raw timeout)")
                return BulkResult(s.id, s.proxy_ip, 0,
                                  int((time.time() - t0) * 1000),
                                  error="raw_get_timeout")
            except Exception as e:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                return BulkResult(s.id, s.proxy_ip, 0,
                                  int((time.time() - t0) * 1000),
                                  error=f"raw:{type(e).__name__}:{e}")
            finally:
                s.in_flight = False

    # ───────── internals ─────────

    async def _fire_bulk_on(self, s: SessionEntry, tcins: list[str],
                            cache_bust: bool = False) -> BulkResult:
        async with s.busy_lock:
            if s.tab is None or s.state != "ready":
                return BulkResult(s.id, s.proxy_ip, 0, 0,
                                  error=f"session_not_ready:{s.state}")
            s.in_flight = True
            s.last_request_at = time.time()
            t0 = time.time()
            js = self._build_bulk_fetch_js(tcins, self.store_id, cache_bust)
            try:
                result = await asyncio.wait_for(
                    s.tab.evaluate(js, await_promise=True, return_by_value=True),
                    timeout=self.tab_eval_timeout_s,
                )
                ms = int((time.time() - t0) * 1000)
                return self._interpret_eval_result(s, result, ms)
            except asyncio.TimeoutError:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                    logger.warning(f"[DISPATCHER] {s.id} flagged crashed after "
                                   f"{s.consecutive_errors} consec errors (timeout)")
                return BulkResult(s.id, s.proxy_ip, 0,
                                  int((time.time() - t0) * 1000),
                                  error="tab_evaluate_timeout")
            except Exception as e:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                return BulkResult(s.id, s.proxy_ip, 0,
                                  int((time.time() - t0) * 1000),
                                  error=f"{type(e).__name__}:{e}")
            finally:
                s.in_flight = False

    def _interpret_eval_result(self, s: SessionEntry, result, ms: int) -> BulkResult:
        if isinstance(result, dict) and "__http_status" in result:
            status = int(result["__http_status"])
            if status == 200:
                s.consecutive_errors = 0
                return BulkResult(s.id, s.proxy_ip, 200, ms,
                                  raw=result.get("__body"))
            # 4xx / 5xx — count as a soft failure for crash threshold, but
            # let ProxyState handle the actual park/burn decision via status code.
            # Fix #5: timestamp 4xx (Shape block/throttle class) so
            # MultiSessionPool.pick_session can steer away from a souring /16.
            if 400 <= status < 500:
                s.recent_4xx.append(time.time())
            s.consecutive_errors += 1
            return BulkResult(
                s.id, s.proxy_ip, status, ms,
                error=(result.get("__body_text") or "")[:500],
            )
        if isinstance(result, dict) and "__err" in result:
            s.consecutive_errors += 1
            return BulkResult(s.id, s.proxy_ip, 0, ms,
                              error=f"js:{result['__err']}")
        s.consecutive_errors += 1
        return BulkResult(s.id, s.proxy_ip, 0, ms,
                          error=f"unexpected_eval_result:{str(result)[:120]}")

    @staticmethod
    def _build_bulk_fetch_js(tcins: list[str], store_id: str,
                             cache_bust: bool = False) -> str:
        """Inside-tab JS that fires a bulk RedSky fetch, awaits the response,
        and returns {__http_status, __body} (or {__err} on JS exception).

        is_bot=false intentionally OMITTED — Shape treats explicit non-bot
        declarations as a bot heuristic (CLAUDE.md 2026-04-25 patch).

        cache_bust=True adds a unique query param + cache:'no-store' so the
        request is an origin read, not an Akamai/RedSky edge-cache HIT. Edge
        HITs can serve a stale OUT_OF_STOCK for the cache TTL and mask a live
        restock — the 2026-05-22 missed-ETB-drop root cause. Used by the
        cloaking-alarm verification probe.
        """
        key = random.choice(REDSKY_API_KEYS)
        tcins_csv = ",".join(tcins)
        cb_param = ("url.searchParams.set('_', String(Date.now()) + "
                    "Math.random().toString(36).slice(2));" if cache_bust else "")
        cache_opt = "cache: 'no-store'," if cache_bust else ""
        # 2026-09-09: RESILIENT_REDSKY_CHANNEL=apps reads the mobile-app aggregation
        # (same parser shape) that public monitors read without the HUMAN captcha
        # that blinded the web channel 94% of the 09-07 run. Default web.
        from src.monitoring import redsky_channel as _rc
        _bulk_url = _rc.bulk_url()
        _xh = _rc.extra_headers_js()
        return f"""(async () => {{
            try {{
                const url = new URL('{_bulk_url}');
                url.searchParams.set('key', '{key}');
                url.searchParams.set('tcins', '{tcins_csv}');
                url.searchParams.set('store_id', '{store_id}');
                url.searchParams.set('pricing_store_id', '{store_id}');
                url.searchParams.set('has_pricing_context', 'true');
                url.searchParams.set('has_promotions', 'true');
                {cb_param}
                const resp = await fetch(url.toString(), {{
                    {cache_opt}
                    credentials: 'include',
                    headers: {{
                        {_xh}
                        'accept': 'application/json',
                        'accept-language': 'en-US,en;q=0.9'
                    }}
                }});
                const txt = await resp.text();
                let body = null;
                try {{ body = JSON.parse(txt); }} catch(e) {{}}
                // Always carry truncated raw text so 4xx error bodies (which
                // ARE valid JSON and would otherwise be discarded) reach Python.
                return {{
                    __http_status: resp.status,
                    __body: body,
                    __body_text: txt.slice(0, 800)
                }};
            }} catch(e) {{ return {{__err: String(e)}}; }}
        }})()"""

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
        return await self._fire_bulk_on(s, chunk)

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

    # ───────── internals ─────────

    async def _fire_bulk_on(self, s: SessionEntry, tcins: list[str]) -> BulkResult:
        async with s.busy_lock:
            if s.tab is None or s.state != "ready":
                return BulkResult(s.id, s.proxy_ip, 0, 0,
                                  error=f"session_not_ready:{s.state}")
            s.in_flight = True
            s.last_request_at = time.time()
            t0 = time.time()
            js = self._build_bulk_fetch_js(tcins, self.store_id)
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
    def _build_bulk_fetch_js(tcins: list[str], store_id: str) -> str:
        """Inside-tab JS that fires a bulk RedSky fetch, awaits the response,
        and returns {__http_status, __body} (or {__err} on JS exception).

        is_bot=false intentionally OMITTED — Shape treats explicit non-bot
        declarations as a bot heuristic (CLAUDE.md 2026-04-25 patch).
        """
        key = random.choice(REDSKY_API_KEYS)
        tcins_csv = ",".join(tcins)
        return f"""(async () => {{
            try {{
                const url = new URL('{REDSKY_BULK}');
                url.searchParams.set('key', '{key}');
                url.searchParams.set('tcins', '{tcins_csv}');
                url.searchParams.set('store_id', '{store_id}');
                url.searchParams.set('pricing_store_id', '{store_id}');
                url.searchParams.set('has_pricing_context', 'true');
                url.searchParams.set('has_promotions', 'true');
                const resp = await fetch(url.toString(), {{
                    credentials: 'include',
                    headers: {{
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

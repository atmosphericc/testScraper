"""
Dispatcher — picks an idle session from the pool, fires the retailer's
fetch JS inside that session's tab, returns a FetchResult.

This is the retailer-agnostic core of the dispatch pipeline. All
retailer-specific knowledge (URL construction, response shape, headers)
lives in the RetailerAdapter the dispatcher is constructed with.

The dispatcher does NOT own the sweep loop or rate control — that's
ResilientChecker's job. The dispatcher is a single-shot operation:
given a list of items, fetch them through one session, return the result.

Concurrency: each session has its own busy_lock owned by the pool. The
dispatcher acquires it for the duration of one fetch and releases on return.
Multiple dispatches against different sessions can run in parallel; multiple
dispatches against the same session are serialized by the lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from src.stack.multi_session_pool import (
    MultiSessionPool,
    SessionEntry,
    CONSECUTIVE_ERROR_RECYCLE_THRESHOLD,
)
from src.stack.retailer_adapter import FetchResult, RetailerAdapter


logger = logging.getLogger(__name__)

DEFAULT_TAB_EVAL_TIMEOUT_S = 15.0


@dataclass
class DispatchResult:
    """Pairs the FetchResult with the session info needed by the checker
    (pinned IP for ProxyState, session id for logging, latency for metrics).
    """
    session_id: str
    pinned_ip: str
    fetch: FetchResult


class Dispatcher:
    """Retailer-agnostic dispatcher. Bind to a pool + an adapter and call
    `dispatch(items)` from the sweep loop.

    Splits items into chunks of `adapter.chunk_size`, picks an idle session
    per dispatch, fires the adapter's fetch JS inside that session's tab,
    returns a DispatchResult wrapping the standard FetchResult.

    Chunk rotation: `dispatch_one_sweep` covers one chunk per call, rotating
    through chunks so over time every item is refreshed at
    sweep_rate / n_chunks.
    """

    def __init__(
        self,
        session_pool: MultiSessionPool,
        adapter: RetailerAdapter,
        items: list[str],
        tab_eval_timeout_s: float = DEFAULT_TAB_EVAL_TIMEOUT_S,
    ):
        self.session_pool = session_pool
        self.adapter = adapter
        self.items = list(items)
        self.tab_eval_timeout_s = tab_eval_timeout_s
        self._chunks: list[list[str]] = self._build_balanced_chunks(
            self.items, max(1, int(adapter.chunk_size))
        )
        self._next_chunk_idx = 0

    @staticmethod
    def _build_balanced_chunks(items: list[str], max_size: int) -> list[list[str]]:
        """Split items into the fewest chunks of size <= max_size, sized as
        evenly as possible. Mirrors Target's _build_balanced_chunks — with
        33 items and max=28, returns [17, 16] (uniform refresh) not [28, 5].

        For chunk_size=1 (Walmart's per-item GraphQL/HTML fetch model), this
        produces one chunk per item.
        """
        if not items:
            return [[]]
        n_chunks = max(1, (len(items) + max_size - 1) // max_size)
        base, extra = divmod(len(items), n_chunks)
        chunks: list[list[str]] = []
        i = 0
        for k in range(n_chunks):
            size = base + (1 if k < extra else 0)
            chunks.append(items[i:i + size])
            i += size
        return chunks

    async def dispatch_one_sweep(self) -> Optional[DispatchResult]:
        """Fire one fetch covering the next chunk through a random ready
        session. Returns None if no session is currently available (the
        sweep loop's backpressure should prevent this in normal operation).
        """
        s = self.session_pool.pick_session()
        if s is None:
            return None
        chunk = self._chunks[self._next_chunk_idx]
        self._next_chunk_idx = (self._next_chunk_idx + 1) % len(self._chunks)
        return await self._fire_on(s, chunk)

    async def _fire_on(
        self, s: SessionEntry, items: list[str]
    ) -> DispatchResult:
        """Fire the adapter's fetch JS inside session `s`'s tab. Returns
        DispatchResult with the standard FetchResult shape.
        """
        async with s.busy_lock:
            if s.tab is None or s.state != "ready":
                return DispatchResult(
                    session_id=s.id,
                    pinned_ip=s.proxy_ip,
                    fetch=FetchResult(
                        http_status=0,
                        error=f"session_not_ready:{s.state}",
                    ),
                )
            s.in_flight = True
            s.last_request_at = time.time()
            t0 = time.time()
            js = self.adapter.build_fetch_js(items)
            try:
                raw = await asyncio.wait_for(
                    s.tab.evaluate(js, await_promise=True, return_by_value=True),
                    timeout=self.tab_eval_timeout_s,
                )
                ms = (time.time() - t0) * 1000.0
                return self._interpret_eval_result(s, raw, ms)
            except asyncio.TimeoutError:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                    logger.warning(
                        "[%s-DISPATCH] %s flagged crashed after %d consec errors (timeout)",
                        self.adapter.name.upper(), s.id, s.consecutive_errors,
                    )
                return DispatchResult(
                    session_id=s.id,
                    pinned_ip=s.proxy_ip,
                    fetch=FetchResult(
                        http_status=0,
                        elapsed_ms=(time.time() - t0) * 1000.0,
                        error="tab_evaluate_timeout",
                    ),
                )
            except Exception as e:
                s.consecutive_errors += 1
                if s.consecutive_errors >= CONSECUTIVE_ERROR_RECYCLE_THRESHOLD:
                    s.state = "crashed"
                return DispatchResult(
                    session_id=s.id,
                    pinned_ip=s.proxy_ip,
                    fetch=FetchResult(
                        http_status=0,
                        elapsed_ms=(time.time() - t0) * 1000.0,
                        error=f"{type(e).__name__}:{e}",
                    ),
                )
            finally:
                s.in_flight = False

    def _interpret_eval_result(
        self, s: SessionEntry, raw, ms: float
    ) -> DispatchResult:
        """Wrap the JS return value as a FetchResult.

        The adapter's fetch JS must return either:
          {__http_status: int, __body_json: dict|null, __body_text: str|null}
        or:
          {__err: str}  (JS exception)
        """
        if isinstance(raw, dict) and "__http_status" in raw:
            status = int(raw["__http_status"])
            fetch = FetchResult(
                http_status=status,
                body_json=raw.get("__body_json"),
                body_text=raw.get("__body_text"),
                elapsed_ms=ms,
            )
            if status == 200 and not self.adapter.is_blocked_response(fetch):
                s.consecutive_errors = 0
            else:
                # Non-200 or blocked body — count as soft failure for crash
                # threshold; ProxyState handles the park/burn decision via
                # the recorded status code.
                s.consecutive_errors += 1
            return DispatchResult(
                session_id=s.id, pinned_ip=s.proxy_ip, fetch=fetch
            )
        if isinstance(raw, dict) and "__err" in raw:
            s.consecutive_errors += 1
            return DispatchResult(
                session_id=s.id,
                pinned_ip=s.proxy_ip,
                fetch=FetchResult(
                    http_status=0,
                    elapsed_ms=ms,
                    error=f"js:{raw['__err']}",
                ),
            )
        s.consecutive_errors += 1
        return DispatchResult(
            session_id=s.id,
            pinned_ip=s.proxy_ip,
            fetch=FetchResult(
                http_status=0,
                elapsed_ms=ms,
                error=f"unexpected_eval_result:{str(raw)[:120]}",
            ),
        )

"""
ResilientChecker — retailer-agnostic stock-monitoring orchestrator.

Responsibilities (none of which are retailer-specific):
  - Owns the MultiSessionPool, Dispatcher, ProxyState
  - Runs the sweep loop at target_aggregate_rps with jittered cadence
  - Manages backpressure (caps outstanding dispatches)
  - Rotates which item chunks are dispatched per sweep
  - Background loops: parked-IP retest, stats heartbeat
  - Tracks per-item state and fires on_in_stock callback on OOS→in_stock
    transitions

All retailer-specific logic flows through the RetailerAdapter:
  - Per-IP RPS ceiling validation at startup
  - Chrome stagger window for the pool
  - fetch JS construction (via Dispatcher → adapter)
  - Response parsing (adapter.parse_response)
  - Block detection (adapter.is_blocked_response → feeds ProxyState)

Per-retailer state file: each retailer gets its own proxy state file
(state/{adapter.name}_proxy_state.json) so a Walmart 403 doesn't burn
the IP for Target.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from pathlib import Path
from typing import Callable, Optional

from src.stack.dispatcher import Dispatcher, DispatchResult
from src.stack.multi_session_pool import MultiSessionPool
from src.stack.proxy_state import ProxyState
from src.stack.retailer_adapter import ItemStatus, RetailerAdapter

logger = logging.getLogger(__name__)

# ── tuning constants ──────────────────────────────────────────────────────
DEFAULT_AGGREGATE_RPS = 2.0
PARKED_RETEST_INTERVAL_S = 300        # 5 min — unpark expired IPs
STATS_LOG_INTERVAL_S = 30
MAX_OUTSTANDING_DISPATCHES = 50       # backpressure cap


class ResilientChecker:
    """Retailer-agnostic stock-monitoring orchestrator.

    Construct with an adapter + the list of items, call .start(), it runs
    until .stop(). Calls on_in_stock(ItemStatus) on each OOS→in_stock
    transition.
    """

    def __init__(
        self,
        adapter: RetailerAdapter,
        proxy_urls: list[str],
        items: list[str],
        on_in_stock: Callable[[ItemStatus], None],
        target_aggregate_rps: float = DEFAULT_AGGREGATE_RPS,
        state_dir: Path = Path("state"),
        first_local_port: int = 25000,
        log_per_request: bool = True,
        preflight: bool = True,
    ):
        self.adapter = adapter
        self.proxy_urls = list(proxy_urls)
        self.items = list(items)
        self.on_in_stock = on_in_stock
        self.target_aggregate_rps = float(target_aggregate_rps)
        self.state_dir = state_dir
        self.first_local_port = first_local_port
        self.log_per_request = log_per_request
        self.preflight = preflight

        state_dir.mkdir(parents=True, exist_ok=True)

        # Per-retailer state file — Walmart and Target won't burn each other's IPs.
        self.proxy_state_path = state_dir / f"{adapter.name}_proxy_state.json"

        # Per-retailer profile root — Walmart's session profiles don't
        # collide with Target's. Sessions inside need a login bootstrap
        # before first prod run (see walmart/walmart_session_bootstrap.py
        # for Walmart's flavor).
        self.profile_root = state_dir / f"{adapter.name}_session_profiles"

        # Built in start()
        self.session_pool: Optional[MultiSessionPool] = None
        self.dispatcher: Optional[Dispatcher] = None
        self.proxy_state: Optional[ProxyState] = None

        # State
        self._item_status: dict[str, ItemStatus] = {
            it: ItemStatus(item_id=it, in_stock=False) for it in self.items
        }
        self._ever_seen_in_stock: set[str] = set()
        self._status_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # Stats
        self._total_dispatched = 0
        self._total_200 = 0
        self._total_4xx = 0
        self._total_other = 0
        self._total_blocked = 0
        self._outstanding = 0
        self._sweep_count = 0
        self._start_time: Optional[float] = None

    # ── public API ────────────────────────────────────────────────────────

    async def start(self):
        # Validate the rate target won't push per-IP over the adapter's ceiling
        n_sessions = max(1, len(self.proxy_urls))
        per_ip_rps = self.target_aggregate_rps / n_sessions
        if per_ip_rps > self.adapter.per_ip_rps_ceiling:
            logger.warning(
                "[%s] per-IP RPS %.2f exceeds adapter ceiling %.2f "
                "(aggregate %.1f / %d sessions). Consider lowering RPS or "
                "adding sessions.",
                self.adapter.name.upper(),
                per_ip_rps,
                self.adapter.per_ip_rps_ceiling,
                self.target_aggregate_rps,
                n_sessions,
            )

        # ProxyState (per retailer)
        self.proxy_state = ProxyState(self.proxy_state_path)

        # Build the persistent Chrome pool. Stagger comes from the adapter
        # but the pool reads it from CHROME_STAGGER_TOTAL_S env var, so we
        # set it for this launch. Safe because Target and Walmart don't run
        # simultaneously (user constraint, 2026-05-16). If a caller already
        # set the env var explicitly we respect that override.
        if "CHROME_STAGGER_TOTAL_S" not in os.environ:
            os.environ["CHROME_STAGGER_TOTAL_S"] = str(self.adapter.chrome_stagger_seconds)

        self.session_pool = MultiSessionPool(
            proxy_urls=self.proxy_urls,
            cookies_jar_path=self.state_dir / f"{self.adapter.name}_cookies_jar.json",
            profile_root=self.profile_root,
            forwarder_base_port=self.first_local_port,
            homepage_url=self.adapter.base_url,
        )
        await self.session_pool.start()

        # Register IPs in ProxyState for status tracking
        ip_to_port = {s.proxy_ip: s.local_port
                      for s in self.session_pool.sessions}
        self.proxy_state.bulk_register(ip_to_port)

        # Dispatcher binds pool + adapter + items
        self.dispatcher = Dispatcher(
            session_pool=self.session_pool,
            adapter=self.adapter,
            items=self.items,
        )

        self._start_time = time.time()
        self._tasks.append(asyncio.create_task(self._sweep_loop(),
                                               name=f"{self.adapter.name}_sweep"))
        self._tasks.append(asyncio.create_task(self._parked_retest_loop(),
                                               name=f"{self.adapter.name}_parked_retest"))
        self._tasks.append(asyncio.create_task(self._stats_loop(),
                                               name=f"{self.adapter.name}_stats"))
        logger.info(
            "[%s] resilient checker started: rps=%.2f items=%d proxies=%d "
            "per_ip_rps=%.3f (ceiling %.2f)",
            self.adapter.name.upper(),
            self.target_aggregate_rps,
            len(self.items),
            len(self.proxy_urls),
            per_ip_rps,
            self.adapter.per_ip_rps_ceiling,
        )

    async def stop(self):
        """Bounded shutdown — every step has a deadline so Ctrl+C reliably
        ends the process."""
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.debug("[%s] task %s cleanup error: %s",
                             self.adapter.name.upper(), t.get_name(), e)
        if self.session_pool is not None:
            try:
                await asyncio.wait_for(self.session_pool.stop(), timeout=20.0)
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("[%s] pool stop did not complete cleanly: %s",
                               self.adapter.name.upper(), e)
        if self.proxy_state is not None:
            try:
                self.proxy_state.save()
            except Exception as e:
                logger.warning("[%s] final proxy_state save failed: %s",
                               self.adapter.name.upper(), e)
        logger.info("[%s] stopped", self.adapter.name.upper())

    def latest(self) -> dict[str, ItemStatus]:
        return dict(self._item_status)

    def stats(self) -> dict:
        elapsed = time.time() - self._start_time if self._start_time else 0
        return {
            "retailer": self.adapter.name,
            "elapsed_s": round(elapsed, 1),
            "total_dispatched": self._total_dispatched,
            "total_200": self._total_200,
            "total_4xx": self._total_4xx,
            "total_other": self._total_other,
            "total_blocked": self._total_blocked,
            "outstanding": self._outstanding,
            "sweep_count": self._sweep_count,
            "actual_rps": round(self._sweep_count / elapsed, 2) if elapsed > 0 else 0,
            "proxy_state": self.proxy_state.stats_summary() if self.proxy_state else {},
            "session_state": (self.session_pool.state_summary()
                              if self.session_pool else {}),
        }

    # ── sweep loop ────────────────────────────────────────────────────────

    async def _sweep_loop(self):
        """Schedule one dispatch every (1/target_aggregate_rps) seconds, with
        ±15% jitter to avoid machine-cadence detection. Dispatches run as
        background tasks so a slow tab.evaluate doesn't block the schedule.
        Backpressure caps outstanding dispatches.
        """
        while not self._stop_event.is_set():
            period = 1.0 / max(0.01, self.target_aggregate_rps)
            jitter = period * random.uniform(-0.15, 0.15)
            sleep_for = max(0.05, period + jitter)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                return
            except asyncio.TimeoutError:
                pass

            self._sweep_count += 1
            if self._outstanding > MAX_OUTSTANDING_DISPATCHES:
                logger.warning(
                    "[%s] backlog=%d > %d, skipping sweep %d",
                    self.adapter.name.upper(),
                    self._outstanding, MAX_OUTSTANDING_DISPATCHES,
                    self._sweep_count,
                )
                continue

            self._outstanding += 1
            asyncio.create_task(
                self._dispatch_one(),
                name=f"{self.adapter.name}_sweep_{self._sweep_count}",
            )

    async def _dispatch_one(self):
        try:
            assert self.dispatcher is not None
            result = await self.dispatcher.dispatch_one_sweep()
            if result is None:
                return    # no session available; quiet skip

            self._total_dispatched += 1
            await self._record_status(result)

            if result.fetch.http_status == 200 and not self.adapter.is_blocked_response(result.fetch):
                self._total_200 += 1
                await self._ingest_response(result)
            elif self.adapter.is_blocked_response(result.fetch):
                self._total_blocked += 1
                if self.log_per_request:
                    logger.info("  ## %s (%s) BLOCKED %dms",
                                result.session_id, result.pinned_ip,
                                int(result.fetch.elapsed_ms))
            elif 400 <= result.fetch.http_status < 500:
                self._total_4xx += 1
                if self.log_per_request:
                    logger.info("  !! %s (%s) http=%d %dms err=%s",
                                result.session_id, result.pinned_ip,
                                result.fetch.http_status,
                                int(result.fetch.elapsed_ms),
                                (result.fetch.error or "")[:80])
            else:
                self._total_other += 1
                if self.log_per_request:
                    logger.info("  ?? %s (%s) http=%d err=%s",
                                result.session_id, result.pinned_ip,
                                result.fetch.http_status,
                                (result.fetch.error or "")[:300])
        except Exception:
            logger.exception("[%s] dispatch error", self.adapter.name.upper())
        finally:
            self._outstanding -= 1

    async def _record_status(self, result: DispatchResult):
        """Feed the dispatch outcome to ProxyState so the per-IP park/burn
        machine has data. Block-detected responses count as 403 for the
        streak counter regardless of actual HTTP code.
        """
        assert self.proxy_state is not None
        if self.adapter.is_blocked_response(result.fetch):
            self.proxy_state.record_status(result.pinned_ip, 403)
            return
        if result.fetch.http_status > 0:
            self.proxy_state.record_status(
                result.pinned_ip, result.fetch.http_status
            )

    async def _ingest_response(self, result: DispatchResult):
        """Parse a successful response through the adapter and fire callbacks
        on OOS→in_stock transitions.
        """
        try:
            parsed: list[ItemStatus] = self.adapter.parse_response(
                result.fetch, self.items
            )
        except Exception:
            logger.exception("[%s] adapter.parse_response failed",
                             self.adapter.name.upper())
            return

        in_stock_transitions: list[ItemStatus] = []
        async with self._status_lock:
            for status in parsed:
                if not status.item_id or status.item_id == "?":
                    continue
                prev = self._item_status.get(status.item_id)
                if prev is None:
                    prev = ItemStatus(item_id=status.item_id, in_stock=False)
                    self._item_status[status.item_id] = prev
                was_in_stock = prev.in_stock
                prev.in_stock = status.in_stock
                prev.title = status.title or prev.title
                prev.price = status.price if status.price is not None else prev.price
                prev.availability_status = (
                    status.availability_status or prev.availability_status
                )
                prev.last_checked_at = time.time()
                if prev.in_stock:
                    self._ever_seen_in_stock.add(status.item_id)
                if prev.in_stock and not was_in_stock:
                    in_stock_transitions.append(prev)

        for status in in_stock_transitions:
            if self.on_in_stock:
                try:
                    self.on_in_stock(status)
                except Exception:
                    logger.exception("[%s] on_in_stock callback failed",
                                     self.adapter.name.upper())

    # ── background loops ──────────────────────────────────────────────────

    async def _parked_retest_loop(self):
        """Periodically force-unpark IPs whose park has expired. The next
        normal dispatch picks one up; if it 403s again, it re-parks.
        """
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=PARKED_RETEST_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            assert self.proxy_state is not None
            due = self.proxy_state.retest_due()
            if not due:
                continue
            logger.info("[%s] retesting %d parked IPs",
                        self.adapter.name.upper(), len(due))
            for entry in due:
                self.proxy_state.force_unpark(entry.pinned_ip)

    async def _stats_loop(self):
        """Heartbeat log of pool health + RPS, every STATS_LOG_INTERVAL_S."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=STATS_LOG_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            s = self.stats()
            logger.info(
                "[%s] stats: rps=%.2f sweeps=%d dispatched=%d 200=%d 4xx=%d "
                "blocked=%d other=%d outstanding=%d",
                self.adapter.name.upper(),
                s["actual_rps"], s["sweep_count"],
                s["total_dispatched"], s["total_200"], s["total_4xx"],
                s["total_blocked"], s["total_other"],
                s["outstanding"],
            )

"""
Resilient stock-check engine — browser-native.

Architecture (post-2026-05-13 rearchitect):
  - MultiSessionPool maintains N permanently-running Chrome instances, one per
    BD ISP IP. Each Chrome owns a long-lived target.com tab.
  - TabDispatcher fires bulk RedSky requests via tab.evaluate(fetch(...))
    inside random ready Chromes. No curl_cffi in the request path.
  - One async _sweep_loop schedules dispatches at the configured rate
    (target_sweeps_per_sec). Each sweep fires asynchronously so a slow tab
    doesn't block others.
  - Behavioral mixin: every Nth sweep navigates a Chrome to a real PDP and
    dwells ~5s, instead of firing the API. Makes traffic shape look like
    "user occasionally browses" rather than "pure API polling".
  - Per-IP 401/403 tracking via ProxyState → park/burn lifecycle.
  - Cloaking alarm watches for all-TCIN-OOS sync (Shape cloaking signature).

Reads:
  - config/proxyIps.json (enabled IPs)
  - config/product_config.json (enabled TCINs — caller's responsibility)

Writes:
  - state/proxy_state.json (per-IP 401/403 history)
  - state/cookies_jar.json (forensic snapshot of per-session cookies)

curl_cffi survives only in proxy_preflight (30-sec startup IP filter).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.monitoring.proxy_preflight import preflight_validate
from src.monitoring.tab_dispatcher import (
    TabDispatcher, BulkResult, REDSKY_BULK, REDSKY_API_KEYS,
)
from src.proxy.proxy_state import ProxyState
from src.session.multi_session_pool import MultiSessionPool

logger = logging.getLogger(__name__)

# ───────── tuning constants ─────────
DEFAULT_TARGET_SWEEPS_PER_SEC = 2.0   # one bulk fetch covers all TCINs at once
DEFAULT_STORE_ID = "865"               # matches the validated in-tab fetch path
PARKED_RETEST_INTERVAL_S = 300         # 5 min — unpark expired-park IPs

# Behavioral mixin: every (1/ratio)-th sweep is a PDP nav instead of bulk fetch.
# Disguises pure-API polling pattern with the look of a user occasionally
# loading a product page. Side benefit: PDP nav refreshes _abck/_px3 cookies.
#
# DEFAULT 0.0 (off) after 2026-05-13 validation: at 3 RPS aggregate, ratio=0.10
# means one PDP nav every 3.3s account-wide, which Shape detected as a bot
# pattern (single 7×403 burst on one session at t=228s during 5-min test).
# Pure API polling sustained 100% for 20 min at the same RPS without behavioral.
# If enabling, prefer 0.02 or lower (1 nav per >15s aggregate).
DEFAULT_BEHAVIORAL_MIX_RATIO = 0.0

# Backpressure: if more than this many dispatches are in-flight, the sweep loop
# pauses scheduling new ones. Prevents runaway tab.evaluate stalls from creating
# unbounded coroutine pile-up.
MAX_OUTSTANDING_DISPATCHES = 50


@dataclass
class TcinStatus:
    tcin: str
    in_stock: bool = False
    last_status_code: int = 0
    last_checked_at: float = 0.0
    availability_status: str = "UNKNOWN"
    title: str = ""
    consecutive_non_200: int = 0
    max_qty: int = 1


class ResilientStockChecker:
    """
    Async stock-check engine. Start with .start(), stop with .stop().
    .latest() returns the current per-TCIN status snapshot.
    """

    def __init__(
        self,
        proxy_urls: list[str],
        tcins: list[str],
        on_in_stock: Optional[Callable[[TcinStatus], None]] = None,
        target_sweeps_per_sec: float = DEFAULT_TARGET_SWEEPS_PER_SEC,
        store_id: str = DEFAULT_STORE_ID,
        state_dir: Path = Path("state"),
        cookies_jar_path: Optional[Path] = None,
        first_local_port: int = 24000,
        log_per_request: bool = True,
        preflight: bool = True,
        preflight_tcin: str = "50270379",
        behavioral_mix_ratio: float = DEFAULT_BEHAVIORAL_MIX_RATIO,
        harvest_via_local_ip: bool = False,
    ):
        self.proxy_urls = list(proxy_urls)
        self.tcins = list(tcins)
        self.on_in_stock = on_in_stock
        self.target_sweeps_per_sec = float(target_sweeps_per_sec)
        self.store_id = store_id
        self.log_per_request = log_per_request
        self.preflight = preflight
        self.preflight_tcin = preflight_tcin
        self.first_local_port = first_local_port
        self.behavioral_mix_ratio = max(0.0, min(0.5, float(behavioral_mix_ratio)))
        self.harvest_via_local_ip = harvest_via_local_ip

        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = state_dir
        self.cookies_jar_path = cookies_jar_path or (state_dir / "cookies_jar.json")
        self.proxy_state = ProxyState(state_dir / "proxy_state.json")

        # Built in start()
        self.multi_session_pool: Optional[MultiSessionPool] = None
        self.dispatcher: Optional[TabDispatcher] = None
        self._verified_urls: list[str] = []
        self._stock_monitor_parser = None     # lazy StockMonitor() for _process_response

        # State
        self._tcin_status: dict[str, TcinStatus] = {t: TcinStatus(tcin=t) for t in self.tcins}
        self._ever_seen_in_stock: set[str] = set()
        self._status_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

        # In TEST_MODE, reset s.in_stock to False after firing the callback so
        # the OOS→in-stock transition re-triggers on the next sweep. Without
        # this, a permanently in-stock TCIN (e.g. the gum test product) only
        # fires one purchase per run because the transition gate at
        # _ingest_bulk_response never re-arms. In production this stays off
        # so we never double-buy on continuous in-stock state.
        self._test_mode_loop = os.environ.get(
            "TEST_MODE", "").strip().lower() in ("1", "true", "yes")

        # Cloaking-alarm verification: when the alarm fires (all-OOS but TCINs
        # were in stock earlier this run), re-probe the ever-in-stock TCINs
        # with a cache-busted origin fetch and fire on_in_stock on a real hit.
        # Default ON — the 2026-05-22 missed-ETB-drop fix. Disable with
        # RESILIENT_VERIFY_ON_CLOAK=0.
        self._verify_on_cloak = os.environ.get(
            "RESILIENT_VERIFY_ON_CLOAK", "1").strip().lower() in ("1", "true", "yes")

        # C0 FIX — close the cold-item detection blind spot. The cloaking-alarm
        # verify above only re-probes _ever_seen_in_stock TCINs, so an item that
        # has NEVER been in stock this run (e.g. the 06-03 Prismatic ETB that
        # dropped in-window and was never detected) gets zero cache-busted reads
        # and a brief drop is invisible. The ground-truth probe already cache-
        # busts the FULL list every 30s; when this flag is on, a TCIN it finds
        # IN_STOCK while the sweep had OOS fires a real purchase instead of just
        # logging. Default ON. Disable with RESILIENT_GROUND_TRUTH_FIRES=0.
        self._ground_truth_fires = os.environ.get(
            "RESILIENT_GROUND_TRUTH_FIRES", "1").strip().lower() in ("1", "true", "yes")

        # Stats
        self._total_dispatched = 0
        self._total_200 = 0
        self._total_403 = 0
        self._total_other = 0
        self._total_behavioral = 0
        self._outstanding = 0
        self._sweep_count = 0
        self._start_time: Optional[float] = None

    # ───────── public API ─────────

    async def start(self):
        if self.preflight:
            logger.info(f"[STOCK] pre-flight: validating {len(self.proxy_urls)} proxies "
                        f"on TCIN {self.preflight_tcin}")
            self._verified_urls = await preflight_validate(
                self.proxy_urls,
                tcin=self.preflight_tcin,
                store_id=self.store_id,
            )
            logger.info(f"[STOCK] pre-flight done: {len(self._verified_urls)}/"
                        f"{len(self.proxy_urls)} verified clean")
            if not self._verified_urls:
                raise RuntimeError("Pre-flight rejected all proxies — none are usable")
        else:
            self._verified_urls = list(self.proxy_urls)

        # Build the persistent Chrome pool
        self.multi_session_pool = MultiSessionPool(
            proxy_urls=self._verified_urls,
            cookies_jar_path=self.cookies_jar_path,
            profile_root=self.state_dir / "session_profiles",
            forwarder_base_port=self.first_local_port,
            harvest_via_local_ip=self.harvest_via_local_ip,
        )
        await self.multi_session_pool.start()

        # Register IPs in ProxyState for 401/403 → park tracking
        ip_to_port = {s.proxy_ip: s.local_port
                      for s in self.multi_session_pool.sessions}
        self.proxy_state.bulk_register(ip_to_port)
        # Burn ONLY IPs we tried this run that failed preflight — not every
        # entry that's not in this run's verified set, which would also burn
        # IPs from prior runs that aren't in today's input. The proxy_state file
        # is shared across runs and shouldn't be destroyed by a partial sweep.
        from src.session.multi_session_pool import _pinned_ip as _extract_ip
        input_ips = {ip for ip in (_extract_ip(u) for u in self.proxy_urls) if ip}
        verified_ips = set(ip_to_port.keys())
        for ip in input_ips - verified_ips:
            self.proxy_state.force_burn(ip)

        # Build the dispatcher
        self.dispatcher = TabDispatcher(
            session_pool=self.multi_session_pool,
            tcins=self.tcins,
            store_id=self.store_id,
        )

        self._start_time = time.time()
        self._tasks.append(asyncio.create_task(self._sweep_loop(), name="sweep_loop"))
        self._tasks.append(asyncio.create_task(self._parked_retest_loop(),
                                               name="parked_retest"))
        self._tasks.append(asyncio.create_task(self._cloaking_alarm_loop(),
                                               name="cloaking_alarm"))
        self._tasks.append(asyncio.create_task(self._stats_loop(), name="stats"))
        # Diagnostic probes (added 2026-06-03). Purpose: make a "missed restock"
        # self-diagnosing (real OOS vs stale edge cache vs Shape cloak). The canary
        # is read-only; the ground-truth probe ALSO fires a real purchase on a
        # cold/stale cache-bust hit when RESILIENT_GROUND_TRUTH_FIRES=1 (default,
        # C0 fix 2026-06-04). Disable both with STOCK_DIAG_PROBES=0.
        if os.environ.get("STOCK_DIAG_PROBES", "1") not in ("0", "false", "False"):
            self._tasks.append(asyncio.create_task(
                self._ground_truth_probe_loop(), name="ground_truth_probe"))
            self._tasks.append(asyncio.create_task(
                self._canary_loop(), name="canary"))
            logger.info("[STOCK] diagnostic probes ON (ground-truth fires purchases "
                        "when RESILIENT_GROUND_TRUTH_FIRES=1; canary read-only) "
                        "— STOCK_DIAG_PROBES=0 to disable")
        logger.info(
            f"[STOCK] started: sweeps/sec={self.target_sweeps_per_sec} "
            f"behavioral_mix={self.behavioral_mix_ratio:.2f} "
            f"tcins={len(self.tcins)} proxies={len(self._verified_urls)}"
        )

    async def stop(self):
        """Bounded shutdown — every step has a deadline so Ctrl+C reliably
        ends the process. Pool stop has its own internal budget; this call
        wraps it again as a belt-and-suspenders against unexpected hangs."""
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.debug(f"[STOCK] task {t.get_name()} cleanup error: {e}")
        if self.multi_session_pool is not None:
            try:
                await asyncio.wait_for(self.multi_session_pool.stop(), timeout=20.0)
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[STOCK] pool stop did not complete cleanly: {e}")
        try:
            self.proxy_state.save()
        except Exception as e:
            logger.warning(f"[STOCK] final proxy_state save failed: {e}")
        logger.info("[STOCK] stopped")

    def latest(self) -> dict[str, TcinStatus]:
        return dict(self._tcin_status)

    def stats(self) -> dict:
        elapsed = time.time() - self._start_time if self._start_time else 0
        return {
            "elapsed_s": round(elapsed, 1),
            "total_dispatched": self._total_dispatched,
            "total_200": self._total_200,
            "total_403": self._total_403,
            "total_other": self._total_other,
            "total_behavioral": self._total_behavioral,
            "outstanding": self._outstanding,
            "sweep_count": self._sweep_count,
            "actual_sweeps_per_sec": round(self._sweep_count / elapsed, 2) if elapsed > 0 else 0,
            "proxy_state": self.proxy_state.stats_summary(),
            "session_state": (self.multi_session_pool.state_summary()
                              if self.multi_session_pool else {}),
        }

    # ───────── inner loops ─────────

    async def _sweep_loop(self):
        """Schedule one dispatch every (1/target_sweeps_per_sec) seconds, with
        ±15% jitter. Dispatches run as background tasks so a slow tab.evaluate
        doesn't block the schedule. Backpressure caps outstanding dispatches."""
        # Behavioral cadence: 1 in N sweeps is a PDP nav (N = round(1/ratio)).
        # ratio=0.1 → every 10th sweep is behavioral. ratio=0 → never.
        behavioral_period = (round(1.0 / self.behavioral_mix_ratio)
                             if self.behavioral_mix_ratio > 0 else 0)

        while not self._stop_event.is_set():
            period = 1.0 / max(0.01, self.target_sweeps_per_sec)
            jitter = period * random.uniform(-0.15, 0.15)
            sleep_for = max(0.05, period + jitter)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                return
            except asyncio.TimeoutError:
                pass

            self._sweep_count += 1
            if self._outstanding > MAX_OUTSTANDING_DISPATCHES:
                logger.warning(f"[STOCK] backlog={self._outstanding} > {MAX_OUTSTANDING_DISPATCHES}, "
                               f"skipping sweep {self._sweep_count}")
                continue

            is_behavioral = (behavioral_period > 0
                             and self._sweep_count % behavioral_period == 0)
            self._outstanding += 1
            asyncio.create_task(
                self._dispatch_one(is_behavioral),
                name=f"sweep_{self._sweep_count}",
            )

    async def _dispatch_one(self, behavioral: bool):
        try:
            if behavioral:
                tcin = random.choice(self.tcins)
                result = await self.dispatcher.dispatch_behavioral_pdp(tcin)
                if result is None:
                    return    # no session available; quiet skip
                self._total_behavioral += 1
                if self.log_per_request:
                    logger.debug(f"[BEHAVIORAL] {result.session_id} pdp={tcin} "
                                 f"http={result.http_status} {result.latency_ms}ms")
                # Behavioral nav: do NOT record into ProxyState — different
                # status distribution from API calls, would skew park heuristic.
                return

            result = await self.dispatcher.dispatch_one_sweep()
            if result is None:
                return    # no session available; quiet skip

            self._total_dispatched += 1
            if result.http_status == 200:
                self._total_200 += 1
                self.proxy_state.record_status(result.pinned_ip, 200)
                if result.raw:
                    await self._ingest_bulk_response(result)
            elif result.http_status in (401, 403):
                self._total_403 += 1
                self.proxy_state.record_status(result.pinned_ip, result.http_status)
                if self.log_per_request:
                    logger.info(f"  !! {result.session_id} ({result.pinned_ip}) "
                                f"http={result.http_status} {result.latency_ms}ms "
                                f"err={(result.error or '')[:80]}")
            else:
                self._total_other += 1
                self.proxy_state.record_status(result.pinned_ip, result.http_status)
                if self.log_per_request:
                    logger.info(f"  ?? {result.session_id} ({result.pinned_ip}) "
                                f"http={result.http_status} err={(result.error or '')[:300]}")
        except Exception:
            logger.exception("[STOCK] dispatch error")
        finally:
            self._outstanding -= 1

    def _parse_bulk(self, raw: dict) -> dict:
        """Run a raw RedSky body through StockMonitor._process_response.
        Shared by the sweep-ingest path and the cloaking-alarm verify probe."""
        if self._stock_monitor_parser is None:
            from src.monitoring.stock_monitor import StockMonitor
            self._stock_monitor_parser = StockMonitor()
        try:
            return self._stock_monitor_parser._process_response(raw, 0)
        except Exception:
            logger.exception("[STOCK] _process_response failed")
            return {}

    async def _ingest_bulk_response(self, result: BulkResult):
        """Parse a 200 bulk response through StockMonitor._process_response
        (reuse — already handles the bulk product_summaries shape correctly),
        update per-TCIN state, fire on_in_stock callback on transitions."""
        if not result.raw:
            return
        parsed = self._parse_bulk(result.raw)

        in_stock_transitions = []
        async with self._status_lock:
            for tcin, info in parsed.items():
                s = self._tcin_status.get(tcin)
                if s is None:
                    s = TcinStatus(tcin=tcin)
                    self._tcin_status[tcin] = s
                was_in_stock = s.in_stock
                s.in_stock = bool(info.get("in_stock"))
                s.last_status_code = 200
                s.last_checked_at = time.time()
                s.availability_status = info.get("availability_status", "UNKNOWN")
                s.title = info.get("title", s.title)
                s.max_qty = int(info.get("max_qty", s.max_qty or 1))
                s.consecutive_non_200 = 0
                if s.in_stock:
                    self._ever_seen_in_stock.add(tcin)
                if s.in_stock and not was_in_stock:
                    in_stock_transitions.append(s)

        for s in in_stock_transitions:
            if self.on_in_stock:
                try:
                    self.on_in_stock(s)
                except Exception:
                    logger.exception("[STOCK] on_in_stock callback failed")
            # TEST_MODE loop: re-arm the OOS→in-stock transition gate so the
            # next sweep re-fires the callback. The purchase manager's
            # active_purchases dedupe filters concurrent-fire signals during
            # an in-flight purchase, so the next callback only lands after
            # the current cycle (cart clear) finishes. Effect: indefinite
            # test loop on a permanently in-stock TCIN.
            if self._test_mode_loop:
                async with self._status_lock:
                    if s.tcin in self._tcin_status:
                        self._tcin_status[s.tcin].in_stock = False

    # ───────── background loops ─────────

    async def _parked_retest_loop(self):
        """Periodically force-unpark IPs whose park has expired. The next
        normal dispatch picks one up; if it 403s again, it re-parks automatically."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=PARKED_RETEST_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            due = self.proxy_state.retest_due()
            if not due:
                continue
            logger.info(f"[STOCK] retesting {len(due)} parked IPs")
            for entry in due:
                self.proxy_state.force_unpark(entry.pinned_ip)

    async def _cloaking_alarm_loop(self):
        """Watchdog: alarm if at least one TCIN that was previously seen in_stock
        during this run has now flipped OOS along with everything else.

        Without the previously-in-stock gate, this fires constantly when reality
        is "everything OOS" (e.g. tracking unreleased Pokemon products) — a
        false positive that drowns the real signal. The cloaking pattern Shape
        exhibits is "session was seeing real stock, now it sees only OOS",
        which requires us to have observed at least one in_stock transition."""
        OOS_THRESHOLD = 2
        oos_streak = 0
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                return
            except asyncio.TimeoutError:
                pass

            async with self._status_lock:
                statuses = [s for s in self._tcin_status.values()
                            if s.last_status_code == 200]
            if len(statuses) < 3:
                continue
            if not self._ever_seen_in_stock:
                # Nothing has ever been in stock this run; can't distinguish
                # "Shape cloaked us" from "everything is genuinely OOS".
                oos_streak = 0
                continue
            all_oos = all(not s.in_stock for s in statuses)
            if all_oos:
                oos_streak += 1
                if oos_streak >= OOS_THRESHOLD:
                    logger.warning(
                        f"[STOCK] CLOAKING ALARM: all {len(statuses)} TCINs reported OOS "
                        f"for {oos_streak} cycles, but {len(self._ever_seen_in_stock)} "
                        f"of them were in_stock earlier this run — verifying via origin fetch."
                    )
                    if self._verify_on_cloak:
                        try:
                            await self._verify_in_stock_candidates()
                        except Exception:
                            logger.exception("[STOCK] verify-on-cloak failed")
            else:
                oos_streak = 0

    async def _verify_in_stock_candidates(self):
        """Cloaking-alarm response. The sweep path reports all-OOS, but the
        bulk RedSky endpoint is edge-cached and can serve a stale OUT_OF_STOCK
        for the cache TTL — masking a live restock (2026-05-22 missed-ETB-drop
        root cause). Re-probe every ever-in-stock TCIN with a cache-busted
        origin fetch; on a real hit, fire on_in_stock so the purchase still
        launches even though the cached sweep path missed it."""
        if self.dispatcher is None or not self._ever_seen_in_stock:
            return
        candidates = sorted(self._ever_seen_in_stock)
        result = await self.dispatcher.dispatch_verify(candidates)
        if result is None or result.http_status != 200 or not result.raw:
            logger.warning(
                f"[STOCK] VERIFY: origin probe for {candidates} returned no "
                f"usable data (http={getattr(result, 'http_status', '?')})"
            )
            return
        parsed = self._parse_bulk(result.raw)
        hits = []
        async with self._status_lock:
            for tcin, info in parsed.items():
                fresh = bool(info.get("in_stock"))
                avail = info.get("availability_status", "UNKNOWN")
                logger.warning(f"[STOCK] VERIFY (cache-bust): {tcin} -> "
                               f"{avail} in_stock={fresh}")
                s = self._tcin_status.get(tcin)
                if s is None:
                    s = TcinStatus(tcin=tcin)
                    self._tcin_status[tcin] = s
                if fresh:
                    was = s.in_stock
                    s.in_stock = True
                    s.availability_status = avail
                    s.last_status_code = 200
                    s.last_checked_at = time.time()
                    s.title = info.get("title", s.title)
                    s.max_qty = int(info.get("max_qty", s.max_qty or 1))
                    self._ever_seen_in_stock.add(tcin)
                    if not was:
                        hits.append(s)
        for s in hits:
            logger.warning(f"[STOCK] VERIFY CONFIRMED IN STOCK: {s.tcin} — "
                           f"sweep path had it OOS (stale cache); firing purchase")
            if self.on_in_stock:
                try:
                    self.on_in_stock(s)
                except Exception:
                    logger.exception("[STOCK] on_in_stock (verify) failed")

    async def _stats_loop(self):
        """Heartbeat: log pool + dispatch stats every 30s."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                return
            except asyncio.TimeoutError:
                pass
            st = self.stats()
            ps = st["proxy_state"]
            ss = st["session_state"]
            logger.info(
                f"[STOCK STATS] t={st['elapsed_s']}s "
                f"sweeps={st['sweep_count']} ({st['actual_sweeps_per_sec']}/s) "
                f"200={st['total_200']} 403={st['total_403']} other={st['total_other']} "
                f"beh={st['total_behavioral']} outstanding={st['outstanding']} "
                f"sessions={ss.get('ready', 0)}r/{ss.get('crashed', 0)}c/{ss.get('recycling', 0)}rc "
                f"pool A={ps['active']} P={ps['parked']} B={ps['burned']}"
            )
            # Per-TCIN visibility for the ever-in-stock set — makes a missed
            # restock diagnosable (raw availability + read-age) instead of
            # inferred. Added after the 2026-05-22 audit.
            now = time.time()
            async with self._status_lock:
                watch = [self._tcin_status[t]
                         for t in sorted(self._ever_seen_in_stock)
                         if t in self._tcin_status]
            for s in watch:
                age = (now - s.last_checked_at) if s.last_checked_at else -1.0
                logger.info(f"[STOCK WATCH] {s.tcin}: in_stock={s.in_stock} "
                            f"avail={s.availability_status} "
                            f"last_clean_read={age:.0f}s ago")

    # ───────── diagnostic probes (ground-truth CAN fire; canary read-only) ─────────
    # Three independent views of stock get logged so a later log review can
    # tell WHY the bot saw OOS during a real drop:
    #   * sweep hot-path  — the live detector (non-cache-busted, pool identity)
    #   * GROUND-TRUTH    — cache-busted read, SAME pool identity. If it sees
    #                       stock the hot-path missed => stale edge cache.
    #   * CANARY          — cache-busted read through an INDEPENDENT identity
    #                       (host-direct, no BD proxy / no pool cookies). If it
    #                       sees stock the pool can't => the pool is being
    #                       served CLOAKED OOS (Shape/Akamai soft-block) — the
    #                       one failure the pool cannot detect about itself.
    # The CANARY loop is observational only (logs, never calls on_in_stock). The
    # GROUND-TRUTH loop also logs, but when RESILIENT_GROUND_TRUTH_FIRES=1 (default)
    # it ALSO calls on_in_stock on a cold/stale cache-bust hit the sweep missed —
    # i.e. it can start a REAL purchase (C0 fix, 2026-06-04). See _ground_truth_probe_loop.

    async def _ground_truth_probe_loop(self):
        """Every 30s: cache-busted read of the FULL TCIN list through the pool;
        flag any TCIN the sweep had OOS but the cache-bust shows IN_STOCK
        (= stale edge cache). Logs always; additionally FIRES on_in_stock for such
        a TCIN when RESILIENT_GROUND_TRUTH_FIRES=1 (default) — the C0 cold/stale
        catch — so this loop can start a real purchase, not just log."""
        INTERVAL_S = 30.0
        cycle = 0
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            cycle += 1
            try:
                if self.dispatcher is None:
                    continue
                result = await self.dispatcher.dispatch_verify(self.tcins)
                if result is None:
                    if cycle % 5 == 1:
                        logger.info("[GROUND-TRUTH] no ready session this cycle (skip)")
                    continue
                if result.http_status != 200 or not result.raw:
                    logger.warning(
                        f"[GROUND-TRUTH] pool cache-bust read FAILED "
                        f"http={result.http_status} — pool may be throttled/tarpitted"
                    )
                    continue
                parsed = self._parse_bulk(result.raw)
                async with self._status_lock:
                    hot = {t: s.in_stock for t, s in self._tcin_status.items()}
                cb_in_stock = sorted(t for t, info in parsed.items()
                                     if info.get("in_stock"))
                stale = []
                for t, info in parsed.items():
                    if info.get("in_stock") and not hot.get(t, False):
                        stale.append(t)
                        logger.warning(
                            f"[GROUND-TRUTH] *** STALE-CACHE *** tcin={t} "
                            f"hotpath=OOS cachebust=IN_STOCK "
                            f"avail={info.get('availability_status')} "
                            f"— sweep was served a stale edge OOS"
                        )
                # C0 FIX — act on full-list cache-bust hits the sweep missed.
                # Covers cold items (never _ever_seen_in_stock) AND stale-cache
                # warm items. Mirrors _verify_in_stock_candidates: update status
                # + fire on a real OOS->in-stock transition. Dedupe is safe — we
                # set in_stock=True so the next cycle won't re-fire, and the
                # purchase manager only starts when status=='ready'. LOG-ONLY
                # behavior preserved when the flag is off.
                if stale and self._ground_truth_fires and self.on_in_stock:
                    fire = []
                    async with self._status_lock:
                        for t in stale:
                            info = parsed.get(t, {})
                            s = self._tcin_status.get(t)
                            if s is None:
                                s = TcinStatus(tcin=t)
                                self._tcin_status[t] = s
                            was = s.in_stock
                            s.in_stock = True
                            s.availability_status = info.get("availability_status", s.availability_status)
                            s.last_status_code = 200
                            s.last_checked_at = time.time()
                            s.title = info.get("title", s.title)
                            s.max_qty = int(info.get("max_qty", s.max_qty or 1))
                            self._ever_seen_in_stock.add(t)
                            if not was:
                                fire.append(s)
                    for s in fire:
                        logger.warning(
                            f"[GROUND-TRUTH] FIRING PURCHASE (cold/stale catch): {s.tcin} "
                            f"— full-list cache-bust IN_STOCK while sweep had OOS"
                        )
                        try:
                            self.on_in_stock(s)
                        except Exception:
                            logger.exception("[GROUND-TRUTH] on_in_stock fire failed")
                if cb_in_stock or stale or cycle % 5 == 1:
                    logger.info(
                        f"[GROUND-TRUTH] pool cache-bust ok: "
                        f"in_stock={cb_in_stock or '[]'} ({len(parsed)} TCINs)"
                    )
                if cycle % 10 == 1:
                    hot_in = sorted(t for t, v in hot.items() if v)
                    logger.info(
                        f"[STOCK TRACE] hotpath in_stock={hot_in or '[]'} of "
                        f"{len(hot)} configured TCINs"
                    )
            except Exception:
                logger.exception("[GROUND-TRUTH] probe cycle failed (non-fatal)")

    async def _canary_loop(self):
        """Every 30s: read the FULL TCIN list through an INDEPENDENT identity
        (host-direct, no BD proxy / no pool cookies). If the clean channel sees
        IN_STOCK while the pool sweep sees OOS => the pool is being served
        CLOAKED data. LOG ONLY."""
        INTERVAL_S = 30.0
        tcins_csv = ",".join(self.tcins)
        cycle = 0
        fail_streak = 0
        fail_403_streak = 0
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            cycle += 1
            try:
                http_status, body = await asyncio.to_thread(self._canary_fetch, tcins_csv)
                if http_status != 200 or not body:
                    # A solid 403 wall is NOT evidence the host IP is blocked:
                    # _canary_fetch is raw urllib (OpenSSL JA3 + Chrome UA), the
                    # exact mismatch Shape flags — the 2026-05-14 proxy audit
                    # proved this transport 403s even from clean IPs. It ran
                    # 403=100% across the 07-06 and 07-08 overnights while the
                    # browser-native pool read the same endpoint at ~99% 200.
                    # Zero signal + a misleading alarm -> retire it for the run.
                    fail_403_streak = fail_403_streak + 1 if http_status == 403 else 0
                    if fail_403_streak >= 20:
                        logger.warning(
                            "[CANARY] 20 consecutive 403s — raw-urllib TLS is "
                            "Shape-flagged (known false positive, 2026-05-14 audit); "
                            "canary disabled for this run. A trustworthy clean-channel "
                            "canary needs a browser-native host-direct read."
                        )
                        return
                    if fail_streak == 0 or fail_streak % 10 == 0:
                        logger.warning(
                            f"[CANARY] clean-channel read failed http={http_status} "
                            f"(streak={fail_streak + 1}) — host IP may itself be "
                            f"blocked, or RedSky key/params changed"
                        )
                    fail_streak += 1
                    continue
                fail_403_streak = 0
                if fail_streak:
                    logger.info(f"[CANARY] clean-channel recovered after {fail_streak} fails")
                    fail_streak = 0
                parsed = self._parse_bulk(body)
                async with self._status_lock:
                    hot = {t: s.in_stock for t, s in self._tcin_status.items()}
                clean_in_stock = sorted(t for t, info in parsed.items()
                                        if info.get("in_stock"))
                cloak = []
                for t, info in parsed.items():
                    if info.get("in_stock") and not hot.get(t, False):
                        cloak.append(t)
                        logger.warning(
                            f"[CANARY] *** CLOAK/FLAG SUSPECTED *** tcin={t} "
                            f"pool=OOS clean=IN_STOCK "
                            f"avail={info.get('availability_status')} "
                            f"— clean identity sees stock the BD pool does not"
                        )
                if clean_in_stock or cloak or cycle % 5 == 1:
                    logger.info(
                        f"[CANARY] clean-channel ok: in_stock={clean_in_stock or '[]'} "
                        f"({len(parsed)} TCINs){' CLOAK!' if cloak else ''}"
                    )
            except Exception:
                logger.exception("[CANARY] cycle failed (non-fatal)")

    def _canary_fetch(self, tcins_csv: str):
        """Blocking RedSky read via the host's DIRECT connection (no BD proxy,
        no pool cookies). Runs in a worker thread (asyncio.to_thread) so it
        never blocks the event loop. Returns (http_status, parsed_json|None)."""
        import urllib.request
        import urllib.error
        import json as _json
        import ssl as _ssl
        key = random.choice(REDSKY_API_KEYS)
        url = (f"{REDSKY_BULK}?key={key}&tcins={tcins_csv}"
               f"&store_id={self.store_id}&pricing_store_id={self.store_id}"
               f"&has_pricing_context=true&has_promotions=true"
               f"&_={int(time.time() * 1000)}{random.randint(1000, 9999)}")
        req = urllib.request.Request(url, headers={
            "accept": "application/json",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36"),
        })
        try:
            with urllib.request.urlopen(
                    req, timeout=12, context=_ssl.create_default_context()) as r:
                return r.status, _json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            return 0, None

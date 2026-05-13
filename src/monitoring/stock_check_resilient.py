"""
Resilient stock-check engine — Refract-style.

Architecture:
  - One ForwarderPool fronts N BD ISP proxies on local ports.
  - ProxyState tracks per-IP visitor_id, 403 streaks, park/burn state.
  - One async dispatch loop fires per-TCIN curl_cffi calls at a configured
    rate (default 3 req/sec) round-robin across (tcin × proxy) tuples.
  - curl_cffi(impersonate="chrome131") gives Chrome JA3/JA4 so Shape's
    primary fingerprint detector passes.
  - Modern endpoint: product_fulfillment_and_variation_hierarchy_v1.
  - Cookies (if present in cookies_jar.json) are attached. Optional —
    the chain works without them but cookies improve trust scoring.
  - Latest per-TCIN status held in memory; in-stock callback fires when
    a TCIN flips from non-in-stock to in-stock.
  - Cloaking detection: alarms if ALL TCINs simultaneously flip OOS.

Reads:
  - config/proxyIps.json (enabled IPs)
  - config/product_config.json (enabled TCINs)
  - state/cookies_jar.json (optional, from harvester)

Writes:
  - state/proxy_state.json (per-IP visitor_id + health)
  - state/stock_check.log (in-memory ring + occasional flush)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from curl_cffi import requests as cffi

from src.monitoring.proxy_preflight import preflight_validate
from src.proxy.local_forwarder import ForwarderPool
from src.proxy.proxy_state import ProxyState

logger = logging.getLogger(__name__)

# Endpoints
REDSKY_MODERN = (
    "https://redsky.target.com/redsky_aggregations/v1/web/"
    "product_fulfillment_and_variation_hierarchy_v1"
)
REDSKY_API_KEYS = [
    "ff457966e64d5e877fdbad070f276d18ecec4a01",
    "9f36aeafbe60771e321a7cc95a78140772ab3e96",
]

# Default policy
DEFAULT_TARGET_RPS = 3.0
DEFAULT_STORE_ID = "3252"
DEFAULT_REQUEST_TIMEOUT = 12.0
COOKIE_REFRESH_INTERVAL_S = 60     # re-read cookies_jar.json at most once a minute
PARKED_RETEST_INTERVAL_S = 300     # background loop retests parked IPs every 5 min
COOLDOWN_AFTER_403_S = 0.5         # short cool-down on a worker after a 403


def _pinned_ip_from_url(url: str) -> str:
    m = re.search(r"-ip-([\d\.]+):", url)
    return m.group(1) if m else ""


@dataclass
class TcinStatus:
    tcin: str
    in_stock: bool = False
    last_status_code: int = 0
    last_checked_at: float = 0.0
    availability_status: str = "UNKNOWN"
    sold_out: Optional[bool] = None
    oos_all: Optional[bool] = None
    title: str = ""
    consecutive_non_200: int = 0


@dataclass
class CheckResult:
    tcin: str
    pinned_ip: str
    http_status: int
    latency_ms: int
    sold_out: Optional[bool] = None
    oos_all: Optional[bool] = None
    availability: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None


class ResilientStockChecker:
    """
    Async dispatch loop. Start with .start(), stop with .stop().
    Provides .latest() for current TCIN statuses.
    """

    def __init__(
        self,
        proxy_urls: list[str],
        tcins: list[str],
        on_in_stock: Optional[Callable[[TcinStatus], None]] = None,
        target_rps: float = DEFAULT_TARGET_RPS,
        store_id: str = DEFAULT_STORE_ID,
        state_dir: Path = Path("state"),
        cookies_jar_path: Optional[Path] = None,
        first_local_port: int = 24000,
        log_per_request: bool = True,
        preflight: bool = True,
        preflight_tcin: str = "50270379",
    ):
        self.proxy_urls = list(proxy_urls)
        self.tcins = list(tcins)
        self.on_in_stock = on_in_stock
        self.target_rps = target_rps
        self.store_id = store_id
        self.log_per_request = log_per_request
        self.preflight = preflight
        self.preflight_tcin = preflight_tcin
        self.first_local_port = first_local_port

        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = state_dir
        self.cookies_jar_path = cookies_jar_path or (state_dir / "cookies_jar.json")
        self.proxy_state = ProxyState(state_dir / "proxy_state.json")

        # Forwarder pool — DEFERRED to start() so pre-flight can filter the
        # proxy list first. Keeps __init__ pure / fast.
        self.pool: Optional[ForwarderPool] = None
        self._verified_urls: list[str] = []

        # State
        self._tcin_status: dict[str, TcinStatus] = {t: TcinStatus(tcin=t) for t in self.tcins}
        self._status_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._tcin_idx = 0
        self._ip_idx = 0
        self._dispatch_lock = asyncio.Lock()

        # Cookies
        self._cookies_cache: dict[str, str] = {}
        self._cookies_cache_at: float = 0.0

        # Stats
        self._total_dispatched = 0
        self._total_200 = 0
        self._total_403 = 0
        self._total_other = 0
        self._start_time: Optional[float] = None

    # ───────── public API ─────────

    async def start(self):
        # ── Pre-flight: probe every proxy once, exclude any that doesn't 200 ──
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

        # ── Build forwarder pool from verified URLs only ──
        self.pool = ForwarderPool()
        ip_to_port: dict[str, int] = {}
        for i, url in enumerate(self._verified_urls):
            port = self.first_local_port + i
            up = self.pool.add_upstream(url, port)
            ip_to_port[up.pinned_ip] = port
        self.proxy_state.bulk_register(ip_to_port)

        # Mark any non-verified IP as burned so the state file is honest
        verified_ips = set(ip_to_port.keys())
        for entry in self.proxy_state.all_entries():
            if entry.pinned_ip not in verified_ips:
                self.proxy_state.force_burn(entry.pinned_ip)

        await self.pool.start_all()
        self._start_time = time.time()
        # Spawn workers — keep concurrency 1-3 to avoid thundering herds.
        worker_count = max(1, min(3, int(self.target_rps)))
        for w in range(worker_count):
            self._tasks.append(asyncio.create_task(
                self._worker_loop(w), name=f"stock_worker_{w}"))
        self._tasks.append(asyncio.create_task(
            self._parked_retest_loop(), name="parked_retest"))
        self._tasks.append(asyncio.create_task(
            self._cloaking_alarm_loop(), name="cloaking_alarm"))
        self._tasks.append(asyncio.create_task(
            self._stats_loop(), name="stats"))
        logger.info(
            f"[STOCK] started: workers={worker_count}, "
            f"target_rps={self.target_rps}, tcins={len(self.tcins)}, "
            f"proxies={len(self.proxy_urls)}"
        )

    async def stop(self):
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"[STOCK] task {t.get_name()} cleanup error: {e}")
        if self.pool is not None:
            await self.pool.stop_all()
        self.proxy_state.save()
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
            "actual_rps": round(self._total_dispatched / elapsed, 2) if elapsed > 0 else 0,
            "proxy_state": self.proxy_state.stats_summary(),
        }

    # ───────── inner loops ─────────

    async def _worker_loop(self, worker_id: int):
        """Pull (tcin, ip) tuples from the round-robin queue and fetch them."""
        # Per-worker target rate (each worker = total_rps / worker_count, roughly)
        worker_count = max(1, min(3, int(self.target_rps)))
        per_worker_interval = worker_count / self.target_rps
        # Start with a small phase offset to de-sync workers
        await asyncio.sleep(random.uniform(0, per_worker_interval))

        while not self._stop_event.is_set():
            tcin, entry = await self._next_dispatch()
            if tcin is None:
                # No active proxies — wait a bit
                await asyncio.sleep(2.0)
                continue

            result = await asyncio.to_thread(
                self._fetch_one, tcin, entry.pinned_ip, entry.local_port,
                entry.visitor_id, self._cookies_for_request(),
            )
            await self._record_result(result)

            # Pace
            jitter = random.uniform(-0.1, 0.1) * per_worker_interval
            sleep_for = max(0.05, per_worker_interval + jitter)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)
                if self._stop_event.is_set():
                    return
            except asyncio.TimeoutError:
                pass

    async def _next_dispatch(self):
        """Round-robin pick of (tcin, proxy_entry). Skips parked/burned IPs."""
        async with self._dispatch_lock:
            actives = self.proxy_state.active_entries()
            if not actives:
                return None, None
            self._ip_idx = (self._ip_idx + 1) % len(actives)
            entry = actives[self._ip_idx]
            self._tcin_idx = (self._tcin_idx + 1) % len(self.tcins)
            tcin = self.tcins[self._tcin_idx]
            return tcin, entry

    async def _record_result(self, result: CheckResult):
        self._total_dispatched += 1
        if result.http_status == 200:
            self._total_200 += 1
        elif result.http_status == 403:
            self._total_403 += 1
        else:
            self._total_other += 1

        self.proxy_state.record_status(result.pinned_ip, result.http_status)

        if result.http_status == 200 and result.sold_out is not None:
            await self._update_tcin_status(result)
        elif result.http_status != 200:
            async with self._status_lock:
                s = self._tcin_status.get(result.tcin)
                if s:
                    s.consecutive_non_200 += 1
                    s.last_status_code = result.http_status
                    s.last_checked_at = time.time()

        if self.log_per_request:
            mark = "OK" if result.http_status == 200 else "!!"
            if result.http_status == 200:
                logger.debug(
                    f"  {mark} {result.tcin} via {result.pinned_ip:<16} "
                    f"http=200 {result.latency_ms}ms "
                    f"avail={result.availability} sold_out={result.sold_out}"
                )
            else:
                logger.info(
                    f"  {mark} {result.tcin} via {result.pinned_ip:<16} "
                    f"http={result.http_status} {result.latency_ms}ms "
                    f"err={result.error}"
                )

    async def _update_tcin_status(self, result: CheckResult):
        in_stock_now = (
            result.sold_out is False
            and result.oos_all is False
            and (result.availability in (None, "IN_STOCK", "PRE_ORDER_SELLABLE"))
        )
        async with self._status_lock:
            s = self._tcin_status[result.tcin]
            was_in_stock = s.in_stock
            s.in_stock = in_stock_now
            s.last_status_code = 200
            s.last_checked_at = time.time()
            s.availability_status = result.availability or "UNKNOWN"
            s.sold_out = result.sold_out
            s.oos_all = result.oos_all
            if result.title:
                s.title = result.title
            s.consecutive_non_200 = 0

        if in_stock_now and not was_in_stock and self.on_in_stock:
            try:
                self.on_in_stock(s)
            except Exception as e:
                logger.exception(f"[STOCK] on_in_stock callback failed: {e}")

    def _cookies_for_request(self) -> dict[str, str]:
        """Read cookies_jar.json if present, cached for COOKIE_REFRESH_INTERVAL_S."""
        now = time.time()
        if now - self._cookies_cache_at < COOKIE_REFRESH_INTERVAL_S:
            return dict(self._cookies_cache)
        try:
            if self.cookies_jar_path.exists():
                data = json.loads(self.cookies_jar_path.read_text(encoding="utf-8"))
                cookies = data.get("cookies", {})
                if isinstance(cookies, dict):
                    self._cookies_cache = cookies
                    self._cookies_cache_at = now
                    return dict(cookies)
        except Exception as e:
            logger.debug(f"[STOCK] cookies jar read failed: {e}")
        return dict(self._cookies_cache)

    def _fetch_one(self, tcin: str, pinned_ip: str, local_port: int,
                   visitor_id: str, cookies: dict[str, str]) -> CheckResult:
        """Sync HTTP via curl_cffi — called from to_thread."""
        local_proxy = f"http://127.0.0.1:{local_port}"
        params = {
            "key": random.choice(REDSKY_API_KEYS),
            "tcin": tcin,
            "store_id": self.store_id,
            "pricing_store_id": self.store_id,
            "has_pricing_store_id": "true",
            "is_bot": "false",
            "channel": "WEB",
            "page": f"/p/A-{tcin}",
            "visitor_id": visitor_id,
        }
        headers = {
            "accept": "application/json",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://www.target.com",
            "referer": f"https://www.target.com/p/A-{tcin}",
            "priority": "u=1, i",
            "sec-ch-ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }
        t0 = time.time()
        try:
            r = cffi.get(
                REDSKY_MODERN,
                params=params,
                headers=headers,
                cookies=cookies if cookies else None,
                proxies={"http": local_proxy, "https": local_proxy},
                timeout=DEFAULT_REQUEST_TIMEOUT,
                impersonate="chrome131",
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                try:
                    j = r.json()
                    product = j.get("data", {}).get("product", {}) or {}
                    f_ = product.get("fulfillment", {}) or {}
                    shipping = f_.get("shipping_options", {}) or {}
                    title = (product.get("item", {}) or {}).get(
                        "product_description", {}).get("title")
                    return CheckResult(
                        tcin=tcin, pinned_ip=pinned_ip,
                        http_status=200, latency_ms=ms,
                        sold_out=f_.get("sold_out"),
                        oos_all=f_.get("is_out_of_stock_in_all_store_locations"),
                        availability=shipping.get("availability_status"),
                        title=title,
                    )
                except Exception as e:
                    return CheckResult(tcin=tcin, pinned_ip=pinned_ip,
                                       http_status=200, latency_ms=ms,
                                       error=f"parse: {e}")
            return CheckResult(tcin=tcin, pinned_ip=pinned_ip,
                               http_status=r.status_code, latency_ms=ms,
                               error=r.text[:120])
        except cffi.exceptions.RequestException as e:
            return CheckResult(tcin=tcin, pinned_ip=pinned_ip,
                               http_status=0, latency_ms=int((time.time() - t0) * 1000),
                               error=f"{type(e).__name__}: {e}")
        except Exception as e:
            return CheckResult(tcin=tcin, pinned_ip=pinned_ip,
                               http_status=0, latency_ms=int((time.time() - t0) * 1000),
                               error=f"{type(e).__name__}: {e}")

    # ───────── background loops ─────────

    async def _parked_retest_loop(self):
        """Periodically retest parked IPs to recover soft-flagged ones."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=PARKED_RETEST_INTERVAL_S)
                if self._stop_event.is_set():
                    return
            except asyncio.TimeoutError:
                pass

            due = self.proxy_state.retest_due()
            if not due:
                continue
            logger.info(f"[STOCK] retesting {len(due)} parked IPs")
            for entry in due:
                # Force-unpark; next normal dispatch will use it. If it 403s,
                # auto-park triggers again.
                self.proxy_state.force_unpark(entry.pinned_ip)

    async def _cloaking_alarm_loop(self):
        """Watchdog: if ALL TCINs simultaneously report OOS for multiple cycles,
        Shape is likely cloaking the session — log alarm. Real-world OOS
        cannot synchronize across many unrelated products."""
        OOS_THRESHOLD = 2     # cycles
        oos_streak = 0
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                if self._stop_event.is_set():
                    return
            except asyncio.TimeoutError:
                pass

            async with self._status_lock:
                statuses = [s for s in self._tcin_status.values()
                            if s.last_status_code == 200]
            if len(statuses) < 3:
                continue
            all_oos = all(not s.in_stock for s in statuses)
            if all_oos:
                oos_streak += 1
                if oos_streak >= OOS_THRESHOLD:
                    logger.warning(
                        f"[STOCK] CLOAKING ALARM: all {len(statuses)} TCINs reported OOS "
                        f"for {oos_streak} cycles. Shape may be cloaking session."
                    )
            else:
                oos_streak = 0

    async def _stats_loop(self):
        """Heartbeat log of pool health every 30s."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                if self._stop_event.is_set():
                    return
            except asyncio.TimeoutError:
                pass
            st = self.stats()
            ps = st["proxy_state"]
            logger.info(
                f"[STOCK STATS] elapsed={st['elapsed_s']}s rps={st['actual_rps']} "
                f"200={st['total_200']} 403={st['total_403']} other={st['total_other']} "
                f"pool active={ps['active']} parked={ps['parked']} burned={ps['burned']}"
            )

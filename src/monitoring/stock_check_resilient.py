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
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.monitoring.proxy_preflight import preflight_validate
from src.monitoring.tab_dispatcher import (
    TabDispatcher, BulkResult, REDSKY_BULK, REDSKY_API_KEYS,
)
from src.monitoring.tcin_visibility import STATE_FILENAME, write_state_atomic
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


def _env_float(name: str, default: float, floor: float = 0.0) -> float:
    try:
        return max(floor, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def captcha_park_seconds(hits: int, base_s: float, max_mult: int = 8) -> float:
    """2026-09-07 per-session captcha park: base x 2^(hits-1), capped at
    base x max_mult (1800 s -> 30 m / 1 h / 2 h / 4 h), floor 60 s."""
    h = max(1, int(hits or 1))
    mult = min(float(max_mult), 2.0 ** (h - 1))
    return max(60.0, float(base_s) * mult)


def effective_sweep_rate(target_rps: float, usable_sessions: int, per_ip_max_rps: float) -> float:
    """Sweep rate = min(target, usable_sessions x per-IP ceiling). With the
    ceiling <= 0 the cap is off; with no usable session the schedule keeps its
    target cadence (dispatch just returns None cheaply until a park expires)."""
    target = max(0.01, float(target_rps))
    if per_ip_max_rps is None or float(per_ip_max_rps) <= 0.0:
        return target
    n = int(usable_sessions or 0)
    if n <= 0:
        return target
    return max(0.01, min(target, n * float(per_ip_max_rps)))


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
    # 2026-09-16 hot-sku plan P1 step 0 (stock probe with hysteresis). Written by
    # note_stock_read() only; read by the purchase manager's stock_snapshot().
    # Nothing in this module branches on them.
    last_true_at: float = 0.0
    last_false_at: float = 0.0
    window_start_at: float = 0.0
    pickup: str = ""


def stock_hyst_s() -> float:
    """TARGET_STOCK_HYST_S (default 20, clamped 5..120): a live stock window
    stays open this long after its last in_stock=True read, so one flickering
    False read cannot end it and window_start_at is not reset by a flicker."""
    try:
        v = float(str(os.environ.get('TARGET_STOCK_HYST_S', '20')).strip())
    except (TypeError, ValueError):
        v = 20.0
    if not (v == v) or v in (float('inf'), float('-inf')):
        v = 20.0
    return min(120.0, max(5.0, v))


def note_stock_read(s, fresh_in, now: float, hyst_s: float) -> None:
    """Pure bookkeeping for one stock read of TcinStatus `s` (plan P1 step 0).

    True read: a new window starts (window_start_at = now) only when there was
    no True read within `hyst_s`; last_true_at = now. False read: last_false_at
    = now. Never raises and never touches in_stock / last_checked_at."""
    try:
        now = float(now)
        if fresh_in:
            lt = float(getattr(s, 'last_true_at', 0.0) or 0.0)
            if not lt or now - lt > float(hyst_s):
                s.window_start_at = now
            s.last_true_at = now
        else:
            s.last_false_at = now
    except Exception:
        pass


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
        on_alert: Optional[Callable[[str, str, str], None]] = None,
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
        self.on_alert = on_alert
        # 2026-09-04 captcha-aware backoff (RESILIENT_CAPTCHA_BACKOFF, default ON).
        # RedSky answers a flagged reader with 403 + {"captchaRelativeURL":...}
        # (F5/Shape ATA). Firing 3/s into that wall deepens the flag (09-04:
        # 1,073 straight 403s, pool never recovered). On a captcha 403 the WHOLE
        # sweep backs off exponentially (x2 per 5 walled reads, cap x16) and the
        # read is NOT handed to ProxyState (its 2-strike 3h park would blind a
        # single-IP sweep). Any 200 resets the multiplier instantly.
        import os as _os
        self._captcha_backoff_on = _os.environ.get('RESILIENT_CAPTCHA_BACKOFF', '1') != '0'
        self._captcha_streak = 0
        self._captcha_backoff_mult = 1.0
        self._captcha_total = 0
        # 2026-09-07 probe verdict (probe_sweep_pool.bat, same fresh profile, same
        # device): 31.105.x / 168.158.x exits -> captcha, 72.56.171.184 -> 200 x2.
        # The wall is IP-RANGE reputation (HUMAN/PerimeterX), so a captcha 403 now
        # parks THAT SESSION (RESILIENT_CAPTCHA_PARK_S, x2 per repeat, cap x8) and
        # the whole-sweep slow-down above applies only when nothing usable is left.
        # The sweep rate is capped at usable_sessions x RESILIENT_PER_IP_MAX_RPS so
        # a lone clean IP is read at 1/s (validated ceiling), never 3/s.
        self._captcha_park_base_s = _env_float('RESILIENT_CAPTCHA_PARK_S', 1800.0, floor=60.0)
        self._per_ip_max_rps = _env_float('RESILIENT_PER_IP_MAX_RPS', 1.0, floor=0.0)
        self._last_usable_logged: Optional[int] = None

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

        # TCIN-VISIBILITY (2026-08-25) -- make "configured but absent from RedSky"
        # LOUD. On the 08-24 overnight run 4 of 13 armed TCINs were absent from
        # every bulk response (unpublished on Target), so a drop on them could
        # never have been detected; the only trace was the every-5th-cycle
        # logger.warning in _ground_truth_probe_loop that nobody read. Now the
        # ground-truth read feeds _note_tcin_visibility: a console banner +
        # logger.error + on_alert callback on first sighting of an invisible
        # TCIN (re-alert every RESILIENT_TCIN_INVISIBLE_REALERT_S while it stays
        # invisible), a NOW VISIBLE line when Target publishes it mid-run, and
        # state/tcin_visibility.json for pre-drop readiness scripts. Purchase
        # behaviour is untouched. RESILIENT_TCIN_VISIBILITY_ALERT=0 restores the
        # old (warning-only) behaviour; RESILIENT_TCIN_VISIBILITY_STATE=0 never
        # writes the state file.
        self._last_seen_at: dict[str, float] = {}
        self._invisible_prev: set[str] = set()
        self._invisible_alerted_at: dict[str, float] = {}
        # 2026-08-25 review hardening: (a) a TCIN counts as INVISIBLE only if it
        # is absent from the cache-bust read AND no 200 response (sweep or
        # ground-truth) has shown it within GRACE_S -- so a partial/odd 200 body
        # cannot fake "every TCIN unpublished" (an unpublished TCIN is never seen
        # at all); (b) alerts are TTL-gated per TCIN in BOTH directions, so a
        # flapping TCIN is bounded to <=2 alerts per REALERT_S; (c) the state
        # file is written on the first successful read so the readiness echo has
        # data within one ground-truth cycle of boot; (d) after GT_FAIL_ALERT_N
        # consecutive failed ground-truth reads the feature announces itself
        # BLIND instead of staying silent (silence must never read as all-visible).
        try:
            self._tcin_vis_grace_s = max(30.0, float(os.environ.get(
                "RESILIENT_TCIN_INVISIBLE_GRACE_S", "90")))
        except ValueError:
            self._tcin_vis_grace_s = 90.0
        self._visible_again_at: dict[str, float] = {}
        self._tcin_vis_written = False
        self._gt_fail_streak = 0
        self._gt_fail_since: Optional[float] = None
        self._gt_fail_alert_n = 10          # 10 x 30 s = 5 min of failed reads
        self._vis_unknown_alerted_at = 0.0
        self._tcin_vis_alert = os.environ.get(
            "RESILIENT_TCIN_VISIBILITY_ALERT", "1").strip().lower() in ("1", "true", "yes")
        try:
            self._tcin_vis_realert_s = max(
                60.0, float(os.environ.get("RESILIENT_TCIN_INVISIBLE_REALERT_S", "3600")))
        except (TypeError, ValueError):
            self._tcin_vis_realert_s = 3600.0
        self._tcin_vis_state = os.environ.get(
            "RESILIENT_TCIN_VISIBILITY_STATE", "1").strip().lower() in ("1", "true", "yes")

        # Stats
        self._total_dispatched = 0
        self._total_200 = 0
        # 2026-09-07: unix time of the pool's last RedSky 200 — read by
        # src/monitoring/tab_fetch_policy.py (RESILIENT_FORCE_TAB_FETCH=auto:
        # the trusted-browser fallback engages only while the pool is blind).
        self._last_200_at = 0.0
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
            "last_200_at": self._last_200_at,
            "usable_sessions": (self.multi_session_pool.usable_session_count()
                                if self.multi_session_pool else 0),
            "captcha_parked": (self.multi_session_pool.captcha_parked_count()
                               if self.multi_session_pool else 0),
            "captcha_total": self._captcha_total,
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
            period = ((1.0 / max(0.01, self._effective_sweeps_per_sec()))       # 09-07 per-IP cap
                      * max(1.0, getattr(self, '_captcha_backoff_mult', 1.0)))  # 09-04 captcha backoff
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

    # ───────── 2026-09-07 per-session captcha park + per-IP rate cap ─────────

    def _effective_sweeps_per_sec(self) -> float:
        pool = self.multi_session_pool
        usable = pool.usable_session_count() if pool is not None else 0
        total = len(pool.sessions) if pool is not None else 0
        eff = effective_sweep_rate(self.target_sweeps_per_sec, usable, self._per_ip_max_rps)
        if usable != self._last_usable_logged:
            self._last_usable_logged = usable
            logger.info(f"[STOCK][RATE] usable sessions {usable}/{total} -> sweep {eff:.2f}/s "
                        f"(target {self.target_sweeps_per_sec:.2f}/s, cap "
                        f"{self._per_ip_max_rps:.2f}/s per IP)")
        return eff

    def _find_session(self, session_id: str):
        pool = self.multi_session_pool
        if pool is None:
            return None
        return next((x for x in pool.sessions if x.id == session_id), None)

    def _park_captcha_session(self, session_id: str, pinned_ip: str) -> tuple[int, int]:
        """Park the Chrome that just got the captcha envelope; returns
        (usable_sessions, total_sessions) after the park."""
        pool = self.multi_session_pool
        if pool is None:
            return 0, 0
        total = len(pool.sessions)
        s = self._find_session(session_id)
        if s is None:
            return pool.usable_session_count(), total
        s.captcha_hits += 1
        park = captcha_park_seconds(s.captcha_hits, self._captcha_park_base_s)
        s.captcha_parked_until = time.time() + park
        usable = pool.usable_session_count()
        logger.warning(f"[STOCK][CAPTCHA-PARK] {session_id} ({pinned_ip}) RedSky captcha wall "
                       f"(hit #{s.captcha_hits}) — parked {park:.0f}s; usable sessions {usable}/{total}")
        if usable == 0:
            logger.error("[STOCK][CAPTCHA-PARK] every session is walled — pool blind until a park "
                         "expires; the trusted-browser tab-fetch (RESILIENT_FORCE_TAB_FETCH=auto) "
                         "takes over after RESILIENT_TAB_FETCH_BLIND_S")
        return usable, total

    def _clear_captcha_session(self, session_id: str, pinned_ip: str) -> None:
        s = self._find_session(session_id)
        if s is None or not (s.captcha_hits or s.captcha_parked_until):
            return
        logger.info(f"[STOCK][CAPTCHA-PARK] {session_id} ({pinned_ip}) reads 200 again — "
                    f"cleared (had {s.captcha_hits} captcha hit(s))")
        s.captcha_hits = 0
        s.captcha_parked_until = 0.0

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
                self._last_200_at = time.time()
                self.proxy_state.record_status(result.pinned_ip, 200)
                self._clear_captcha_session(result.session_id, result.pinned_ip)
                if self._captcha_backoff_mult > 1.0 or self._captcha_streak:
                    logger.info(f"[STOCK][CAPTCHA-BACKOFF] recovered: 200 from {result.session_id} "
                                f"after {self._captcha_streak} walled reads — sweep period back to x1")
                    self._captcha_streak = 0
                    self._captcha_backoff_mult = 1.0
                if result.raw:
                    await self._ingest_bulk_response(result)
            elif result.http_status in (401, 403):
                self._total_403 += 1
                _err = (result.error or '')
                # 2026-09-09: match the DISTINCTIVE RedSky/PX captcha envelope, not a
                # bare 'captcha' substring — an unrelated 403 body merely containing the
                # word would park a sweep IP for 30 min-4 h (per-session park has no early
                # re-test). RedSky returns {"captchaRelativeURL":"/captcha?trackingId=..."}.
                if (self._captcha_backoff_on and result.http_status == 403
                        and any(s in _err.lower() for s in
                                ('captcharelativeurl', 'px-captcha', '/captcha'))):
                    self._captcha_total += 1
                    self._captcha_streak += 1
                    # 2026-09-07: park THIS session; slow the whole sweep only when
                    # no usable session remains (then the auto tab-fetch takes over).
                    _usable, _total = self._park_captcha_session(result.session_id, result.pinned_ip)
                    new_mult = (float(min(16.0, 2.0 ** min(4, self._captcha_streak // 5)))
                                if _usable == 0 else 1.0)
                    if new_mult != self._captcha_backoff_mult:
                        self._captcha_backoff_mult = new_mult
                        logger.warning(f"[STOCK][CAPTCHA-BACKOFF] {result.session_id} ({result.pinned_ip}) "
                                       f"RedSky captcha wall (streak={self._captcha_streak}, "
                                       f"total={self._captcha_total}) — sweep period x{new_mult:.0f}")
                else:
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
        now = time.time()
        _hyst = stock_hyst_s()
        async with self._status_lock:
            for tcin, info in parsed.items():
                self._last_seen_at[str(tcin)] = now   # TCIN-VISIBILITY (2026-08-25)
                s = self._tcin_status.get(tcin)
                if s is None:
                    s = TcinStatus(tcin=tcin)
                    self._tcin_status[tcin] = s
                was_in_stock = s.in_stock
                s.in_stock = bool(info.get("in_stock"))
                s.last_status_code = 200
                s.last_checked_at = time.time()
                note_stock_read(s, s.in_stock, s.last_checked_at, _hyst)
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
        # 2026-08-02 (07-31→08-02 audit): _ever_seen_in_stock stays truthy for
        # the rest of the run after any restock, so post-drop this alarm fired
        # EVERY 30s cycle for 62.5h (~7.5k alarms, ~67k extra origin cache-bust
        # fetches) re-confirming the same all-OOS. A verify that SUCCEEDS with
        # zero hits now backs the next verify off 60s → 120s → 240s → cap 300s;
        # any in-stock observation (sweep or verify hit) or a FAILED probe
        # restores the sharp 30s cadence. Cold catches stay covered while
        # backed off by the ground-truth cache-bust loop (~2.5 min cycle).
        # Kill-switch: TARGET_CLOAK_VERIFY_BACKOFF=0.
        _verify_backoff_s = 0.0
        _next_verify_ts = 0.0
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
                    _backoff_on = os.environ.get(
                        'TARGET_CLOAK_VERIFY_BACKOFF', '1') == '1'
                    if _backoff_on and time.time() < _next_verify_ts:
                        continue
                    logger.warning(
                        f"[STOCK] CLOAKING ALARM: all {len(statuses)} TCINs reported OOS "
                        f"for {oos_streak} cycles, but {len(self._ever_seen_in_stock)} "
                        f"of them were in_stock earlier this run — verifying via origin fetch."
                    )
                    if self._verify_on_cloak:
                        hits = None
                        try:
                            hits = await self._verify_in_stock_candidates()
                        except Exception:
                            logger.exception("[STOCK] verify-on-cloak failed")
                        if _backoff_on:
                            if hits is not None and not hits:
                                # Successful probe, zero hits: all-OOS CONFIRMED
                                # at origin — widen the re-verify interval.
                                _verify_backoff_s = min(
                                    300.0, max(60.0, _verify_backoff_s * 2))
                                _next_verify_ts = time.time() + _verify_backoff_s
                                logger.info(
                                    f"[STOCK] cloak-verify confirmed all-OOS — "
                                    f"next origin verify in {_verify_backoff_s:.0f}s")
                            else:
                                # Real hit or probe failure: stay sharp.
                                _verify_backoff_s = 0.0
                                _next_verify_ts = 0.0
            else:
                oos_streak = 0
                _verify_backoff_s = 0.0
                _next_verify_ts = 0.0

    async def _verify_in_stock_candidates(self):
        """Cloaking-alarm response. The sweep path reports all-OOS, but the
        bulk RedSky endpoint is edge-cached and can serve a stale OUT_OF_STOCK
        for the cache TTL — masking a live restock (2026-05-22 missed-ETB-drop
        root cause). Re-probe every ever-in-stock TCIN with a cache-busted
        origin fetch; on a real hit, fire on_in_stock so the purchase still
        launches even though the cached sweep path missed it."""
        if self.dispatcher is None or not self._ever_seen_in_stock:
            return None
        candidates = sorted(self._ever_seen_in_stock)
        result = await self.dispatcher.dispatch_verify(candidates)
        if result is None or result.http_status != 200 or not result.raw:
            logger.warning(
                f"[STOCK] VERIFY: origin probe for {candidates} returned no "
                f"usable data (http={getattr(result, 'http_status', '?')})"
            )
            return None
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
                    note_stock_read(s, True, s.last_checked_at, stock_hyst_s())
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
        # 2026-08-02: the alarm loop backs off its verify cadence on a
        # CONFIRMED all-OOS (successful probe, zero hits) — return the hit
        # list so it can tell confirmation apart from probe failure (None).
        return hits

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
                    self._note_ground_truth_failure("no ready session")
                    continue
                if result.http_status != 200 or not result.raw:
                    logger.warning(
                        f"[GROUND-TRUTH] pool cache-bust read FAILED "
                        f"http={result.http_status} — pool may be throttled/tarpitted"
                    )
                    self._note_ground_truth_failure(f"http={result.http_status}")
                    continue
                parsed = self._parse_bulk(result.raw)
                # 2026-07-30: name the configured TCINs RedSky returns nothing for
                # (pre-release/unpublished or typo'd) — they are INVISIBLE to both
                # the sweep and this cache-bust read, so a drop on them can never
                # trigger. Same cadence as the "ok" line below (every 5th cycle).
                missing = sorted(set(map(str, self.tcins)) - set(map(str, parsed)))
                if missing and cycle % 5 == 1:
                    logger.warning(
                        f"[GROUND-TRUTH] {len(missing)} configured TCIN(s) absent "
                        f"from RedSky bulk response — invisible to detection: {missing}"
                    )
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
                            note_stock_read(s, True, s.last_checked_at, stock_hyst_s())
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
                # TCIN-VISIBILITY (2026-08-25): placed AFTER the C0 fire loop so
                # its bookkeeping (one small JSON write) can never delay a real
                # purchase trigger. The cache-bust read counts as a sighting;
                # any absence is made LOUD (banner/alert/state file). Never
                # raises, never touches on_in_stock or purchase state.
                _vis_now = time.time()
                if parsed:
                    _resumed = self._gt_fail_streak >= self._gt_fail_alert_n
                    if _resumed:
                        logger.warning(
                            f"[TCIN-VISIBILITY] verification RESUMED after "
                            f"{self._gt_fail_streak} consecutive failed ground-truth reads"
                        )
                    self._gt_fail_streak = 0
                    self._gt_fail_since = None
                    self._vis_unknown_alerted_at = 0.0   # next blind period alerts at once
                    for _t in parsed:
                        self._last_seen_at[str(_t)] = _vis_now
                    # a RESUMED cycle force-writes so the verified=false marker is
                    # replaced at once, not up to 4 cycles later (round-3 review)
                    self._note_tcin_visibility(missing, _vis_now,
                                               force_write=(cycle % 5 == 1) or _resumed)
                else:
                    # A 200 whose body parsed to NOTHING is a failed read, not
                    # "every TCIN unpublished" (2026-08-25 review): skip the
                    # visibility update this cycle (the stale/fire logic above
                    # was a no-op on the empty parse).
                    self._note_ground_truth_failure("http=200 but 0 TCINs parsed")
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

    def _note_ground_truth_failure(self, reason: str) -> None:
        """TCIN-VISIBILITY (2026-08-25 review): the visibility verdict can only
        be computed from a SUCCESSFUL ground-truth read. Count consecutive
        failures and, past GT_FAIL_ALERT_N (~5 min), say so once per REALERT_S
        so silence is never mistaken for "all TCINs visible" (e.g. >30 armed
        TCINs -> unchunked verify read 400s every cycle; pool never ready).
        Sync, never raises, never touches purchase state."""
        try:
            self._gt_fail_streak += 1
            now = time.time()
            if self._gt_fail_since is None:
                self._gt_fail_since = now
            if self._gt_fail_streak < self._gt_fail_alert_n:
                return
            if (now - self._vis_unknown_alerted_at) < self._tcin_vis_realert_s:
                return
            self._vis_unknown_alerted_at = now
            # Persist the blind state FIRST, and independently of the ALERT flag
            # (round-3 review): a run that never verifies must not let the next
            # pre-drop echo read the previous run's verdict as current, even
            # when the operator silenced the banner. Readers treat
            # verified=false as UNKNOWN.
            if self._tcin_vis_state:
                self._write_visibility_state(now, verified=False)
            if not self._tcin_vis_alert:
                return
            message = (
                f"[TCIN-VISIBILITY] UNKNOWN -- {self._gt_fail_streak} consecutive ground-truth "
                f"reads failed (last: {reason}); TCIN visibility is NOT being verified. "
                f"If this persists, the invisible-TCIN check is blind for this run "
                f"(>30 configured TCINs, throttled pool, or no ready session)."
            )
            logger.error(message)
            if self.on_alert:
                try:
                    self.on_alert("tcin_visibility_unknown", "warning", message)
                except Exception:
                    logger.debug("[TCIN-VISIBILITY] on_alert raised (ignored)", exc_info=True)
            self._safe_print(message)
        except Exception:
            logger.exception("[TCIN-VISIBILITY] non-fatal (failure note)")

    @staticmethod
    def _safe_print(*lines: str) -> None:
        """print() that cannot raise into the ground-truth loop (a closed/broken
        stdout -- the 08-14 tee close-race class -- must not block bookkeeping)."""
        for line in lines:
            try:
                print(line)
            except Exception:
                pass

    def _write_visibility_state(self, now: float, verified: bool) -> bool:
        """Build + atomically write <state_dir>/tcin_visibility.json (schema 1).
        verified=False marks a run whose ground-truth reads are failing: the
        lists are the last known ones and readers must report UNKNOWN."""
        configured = sorted(map(str, self.tcins))
        invisible = sorted(self._invisible_prev)
        payload = {
            "schema": 1,
            "updated_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "updated_at_unix": float(now),
            "run_started_at_unix": self._start_time,
            "configured": configured,
            "visible": [t for t in configured if t not in self._invisible_prev],
            "invisible": invisible,
            "last_seen_unix": {t: self._last_seen_at.get(t) for t in configured},
            "verified": bool(verified),
            "gt_fail_streak": int(self._gt_fail_streak),
            "verification_failed_since_unix": self._gt_fail_since if not verified else None,
        }
        return write_state_atomic(self.state_dir / STATE_FILENAME, payload)

    def _note_tcin_visibility(self, missing: list[str], now: float,
                              force_write: bool = False) -> dict:
        """TCIN-VISIBILITY (2026-08-25): record which configured TCINs are
        INVISIBLE to RedSky. `missing` = TCINs absent from the latest full-list
        cache-bust read; a TCIN is INVISIBLE only if it is absent AND has not
        been seen by ANY 200 response (sweep or ground-truth) within
        RESILIENT_TCIN_INVISIBLE_GRACE_S (default 90 s) -- an unpublished TCIN is
        never seen, while a partial/odd body must not flag TCINs the sweep saw a
        second ago. Alerts (banner + logger.error + on_alert) are TTL-gated per
        TCIN (RESILIENT_TCIN_INVISIBLE_REALERT_S) in both directions (flap-proof);
        NOW VISIBLE fires only for a TCIN that had an invisible alert. Writes
        <state_dir>/tcin_visibility.json on change, on force_write, and on the
        first successful read. Sync, never raises, never touches on_in_stock /
        purchase state."""
        newly_missing: set[str] = set()
        became_visible: set[str] = set()
        due: list[str] = []
        vis_due: list[str] = []
        invisible: list[str] = []
        changed = False
        try:
            configured = sorted(map(str, self.tcins))
            absent = set(map(str, missing))
            invisible_set: set[str] = set()
            for t in configured:
                if t in absent:
                    seen = self._last_seen_at.get(t)
                    if seen is None or (now - seen) > self._tcin_vis_grace_s:
                        invisible_set.add(t)
            invisible = sorted(invisible_set)
            visible = [t for t in configured if t not in invisible_set]
            newly_missing = invisible_set - self._invisible_prev
            became_visible = self._invisible_prev - invisible_set
            # First-ever sighting fires at once (default 0.0); a re-flap inside
            # the TTL stays quiet. _invisible_alerted_at is never popped.
            due = [t for t in invisible
                   if (now - self._invisible_alerted_at.get(t, 0.0)) >= self._tcin_vis_realert_s]

            if self._tcin_vis_alert and due:
                message = (
                    f"[TCIN-VISIBILITY] {len(invisible)} of {len(configured)} configured TCIN(s) "
                    f"are INVISIBLE to RedSky (absent from the bulk response) -- a drop on them "
                    f"CANNOT be detected until Target publishes them: {invisible}. Verify each "
                    f"number at the source (typo => never fires; unpublished => auto-appears "
                    f"mid-run and this bot will log NOW VISIBLE)."
                )
                for t in invisible:            # stamp BEFORE any I/O (review)
                    self._invisible_alerted_at[t] = now
                bang = "!" * 70
                logger.error(message)
                if self.on_alert:
                    try:
                        self.on_alert("tcin_invisible", "error", message)
                    except Exception:
                        logger.debug("[TCIN-VISIBILITY] on_alert raised (ignored)", exc_info=True)
                self._safe_print(
                    bang, message,
                    f"[TCIN-VISIBILITY] state: {self.state_dir / STATE_FILENAME} -- "
                    "pre-drop check: venv\\Scripts\\python.exe check_session_readiness.py",
                    bang)

            # NOW VISIBLE: only for TCINs that actually had an invisible alert,
            # TTL-gated on the success side too (bounded even if it flaps).
            vis_due = [t for t in sorted(became_visible)
                       if t in self._invisible_alerted_at
                       and (now - self._visible_again_at.get(t, 0.0)) >= self._tcin_vis_realert_s]
            if self._tcin_vis_alert and vis_due:
                message = (
                    f"[TCIN-VISIBILITY] NOW VISIBLE in RedSky: {vis_due} -- "
                    f"Target published it; detection is live for it from this cycle."
                )
                for t in vis_due:              # stamp BEFORE any I/O (review)
                    self._visible_again_at[t] = now
                logger.warning(message)
                if self.on_alert:
                    try:
                        self.on_alert("tcin_visible_again", "success", message)
                    except Exception:
                        logger.debug("[TCIN-VISIBILITY] on_alert raised (ignored)", exc_info=True)
                self._safe_print(message)

            self._invisible_prev = invisible_set
            changed = bool(newly_missing or became_visible)

            if self._tcin_vis_state and (changed or force_write or not self._tcin_vis_written):
                if self._write_visibility_state(now, verified=True):
                    self._tcin_vis_written = True
        except Exception:
            logger.exception("[TCIN-VISIBILITY] non-fatal")
        return {
            "newly_missing": sorted(newly_missing),
            "became_visible": sorted(became_visible),
            "invisible": list(invisible),
            "alerted": list(due) if (self._tcin_vis_alert and due) else [],
            "visible_alerted": list(vis_due) if (self._tcin_vis_alert and vis_due) else [],
            "changed": changed,
        }

    async def _canary_loop(self):
        """Every 30s: read the FULL TCIN list through an INDEPENDENT identity
        (host-direct, no BD proxy / no pool cookies). If the clean channel sees
        IN_STOCK while the pool sweep sees OOS => the pool is being served
        CLOAKED data. LOG ONLY."""
        # 2026-09-15 kill-switch. This read is raw urllib from the HOME IP (the
        # primary purchase account's exit) every 30 s with a mismatched TLS/UA —
        # the exact bot-shaped hit Shape/HUMAN score — and it has never produced
        # a true cloak positive (06-09 H3 refuted). run_20260914: ~1,700 HTTP 435
        # rejections vs 62 OKs. STOCK_CANARY=0 retires ONLY this loop; the
        # ground-truth cache-bust probe stays ON.
        if os.environ.get("STOCK_CANARY", "1") in ("0", "false", "False"):
            logger.info("[CANARY] disabled (STOCK_CANARY=0) — ground-truth probe still ON")
            return
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
                    # 2026-09-15: any HTTP-level rejection counts, not just 403 —
                    # Target now answers 435 to this transport and the 403-only
                    # rule let it spam all night (run_20260914: 188 WARNs).
                    fail_403_streak = fail_403_streak + 1 if http_status >= 400 else 0
                    if fail_403_streak >= 20:
                        logger.warning(
                            f"[CANARY] 20 consecutive HTTP {http_status} rejections — raw-urllib TLS is "
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

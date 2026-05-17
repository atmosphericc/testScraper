"""
Multi-session pool — persistent Chrome instances per BD IP.

Each session owns:
  - A persistent profile dir (cookies survive across recycles)
  - A dedicated BD proxy IP (via local CONNECT forwarder)
  - A long-lived Chrome browser + tab
  - Its own cookie set, refreshed via tab navigation (not browser relaunch)

Workers dispatch RedSky stock checks via tab.evaluate(fetch(...)) inside the
long-lived tab. The Chrome supplies real JA3/JA4 + accumulated session
trust (live cookies, real visitor_id). The forwarder + per-session profile
isolation makes each session look like a distinct returning Target user.

Lifecycle:
  - start(): launches all sessions staggered over CHROME_STAGGER_TOTAL_S seconds
  - _keepalive_loop: each session re-navigates target.com homepage once per
    refresh_interval_per_session_s (default 1800s = 30 min), round-robin
  - _watchdog_loop: every 30s, recycles any session in state="crashed"
  - stop(): cleanly tears down all browsers and the forwarder pool
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import zendriver as uc
from zendriver import cdp

from src.stack.local_forwarder import ForwarderPool, parse_bd_proxy_url


# ───────────────────────────────────────────────────────────────────────────
# Zendriver compatibility patch
# ───────────────────────────────────────────────────────────────────────────
# Newer Chrome builds (131+) sometimes omit `sameParty`, `sourceScheme`, and
# `sourcePort` from Network.getAllCookies responses. Zendriver's stock
# `Cookie.from_json` does `json["sameParty"]` (no default) and KeyErrors,
# which causes the response listener to die and the `await tab.send(...)`
# call to hang until timeout. Patch once at import time so every cookie
# fetch in this process is safe.
def _patch_zendriver_cookie_from_json():
    from zendriver.cdp import network as _zdn
    _orig = _zdn.Cookie.from_json
    if getattr(_orig, "_resilient_patched", False):
        return

    def _safe_from_json(cls, json):
        return cls(
            name=str(json["name"]),
            value=str(json["value"]),
            domain=str(json["domain"]),
            path=str(json["path"]),
            size=int(json["size"]),
            http_only=bool(json.get("httpOnly", False)),
            secure=bool(json.get("secure", False)),
            session=bool(json.get("session", False)),
            priority=_zdn.CookiePriority.from_json(json["priority"])
                if "priority" in json else _zdn.CookiePriority("Medium"),
            same_party=bool(json.get("sameParty", False)),
            source_scheme=_zdn.CookieSourceScheme.from_json(json["sourceScheme"])
                if "sourceScheme" in json else _zdn.CookieSourceScheme("Unset"),
            source_port=int(json.get("sourcePort", -1)),
            expires=float(json["expires"]) if json.get("expires") is not None else None,
            same_site=_zdn.CookieSameSite.from_json(json["sameSite"])
                if json.get("sameSite") is not None else None,
            partition_key=_zdn.CookiePartitionKey.from_json(json["partitionKey"])
                if json.get("partitionKey") is not None else None,
            partition_key_opaque=bool(json["partitionKeyOpaque"])
                if json.get("partitionKeyOpaque") is not None else None,
        )
    _safe_from_json._resilient_patched = True
    _zdn.Cookie.from_json = classmethod(_safe_from_json)

_patch_zendriver_cookie_from_json()


def _patch_zendriver_client_security_state():
    """Chrome 148+ renamed `privateNetworkRequestPolicy` →
    `localNetworkAccessRequestPolicy` in the ClientSecurityState CDP payload.
    Older zendriver builds (which we're pinned to) unconditionally access the
    old key and KeyError on every Network.requestWillBeSentExtraInfo event —
    the listener_loop catches it but logs each one at INFO with a full
    traceback, flooding 8h-run logs.

    Inject the renamed field from the new one (or a sensible default) before
    delegating, eliminating the spam at the source.
    """
    from zendriver.cdp import network as _zdn
    _orig = _zdn.ClientSecurityState.from_json
    if getattr(_orig, "_resilient_patched", False):
        return

    def _safe_from_json(cls, json):
        if "privateNetworkRequestPolicy" not in json:
            json = {**json,
                    "privateNetworkRequestPolicy":
                        json.get("localNetworkAccessRequestPolicy", "Allow")}
        return _orig.__func__(cls, json)

    _safe_from_json._resilient_patched = True
    _zdn.ClientSecurityState.from_json = classmethod(_safe_from_json)


_patch_zendriver_client_security_state()

logger = logging.getLogger(__name__)

HARVESTER_RELEVANT_COOKIES = {
    "visitorId", "_px2", "_px3", "_pxvid", "_pxhd", "pxcts",
    "_abck", "bm_sz", "bm_sv", "bm_so", "bm_ni",
    "TealeafAkaSid", "3YCzT93n",
    "_tgt_session", "sapphire", "ffsession",
    "UserLocation", "fiatsCookie", "crl8.fpcuid",
}

DEFAULT_REFRESH_INTERVAL_PER_SESSION_S = 1800     # each session keepalive every 30 min
SETTLE_AFTER_NAV_S = 8
HOMEPAGE_URL = "https://www.target.com"
DEFAULT_FORWARDER_BASE_PORT = 22000

# Stagger window for initial 22-Chrome launch — prevents Shape from seeing
# 22 fresh sessions appear in a coordinated burst.
DEFAULT_STAGGER_TOTAL_S = 600          # 10 min spread (envvar CHROME_STAGGER_TOTAL_S overrides)

# Watchdog runs every WATCHDOG_INTERVAL_S; recycles at most one session per tick.
WATCHDOG_INTERVAL_S = 30
RECYCLE_COOLDOWN_S = 60                # min time between recycle attempts per session

# When this many consecutive errors hit a session in TabDispatcher, the
# dispatcher flips s.state="crashed" so the watchdog will recycle it.
CONSECUTIVE_ERROR_RECYCLE_THRESHOLD = 5


def _pinned_ip(url: str) -> str:
    m = re.search(r"-ip-([\d\.]+):", url)
    return m.group(1) if m else ""


@dataclass
class SessionEntry:
    id: str
    proxy_url: str
    proxy_ip: str
    local_port: int
    profile_dir: Path
    visitor_id: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    harvested_at: float = 0.0
    last_refresh_attempt: float = 0.0
    failed_refreshes: int = 0
    # ── persistent-mode fields ──
    browser: Any = None                  # uc.Browser, kept alive across requests
    tab: Any = None                      # long-lived target.com tab on this Chrome
    busy_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_flight: bool = False              # cheap non-blocking check before pick
    last_request_at: float = 0.0
    last_homepage_nav_at: float = 0.0
    consecutive_errors: int = 0
    crash_count: int = 0
    state: str = "starting"              # starting | ready | refreshing | crashed | recycling
    # When True, the pool's keepalive heartbeat MUST skip this session because
    # re-navigating to the homepage would discard a queue ticket the session is
    # currently holding. Set by the queue handler before entering queue;
    # cleared on admission, eviction, or timeout. Also gates pick_session so
    # a queueing session isn't returned for a normal stock-check dispatch.
    in_queue: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "proxy_ip": self.proxy_ip,
            "visitor_id": self.visitor_id,
            "cookies": self.cookies,
            "harvested_at": self.harvested_at,
            "state": self.state,
            "crash_count": self.crash_count,
        }


class MultiSessionPool:
    """
    Maintains N permanently-running Chrome instances for the TabDispatcher
    to fire stock checks against. Each Chrome stays up across all requests;
    a keepalive loop refreshes session cookies by re-navigating the existing
    tab back to target.com on a schedule (no browser restart).

    A watchdog recycles individual Chromes that crash (one at a time, with a
    cooldown) without disturbing the rest of the pool.
    """

    def __init__(
        self,
        proxy_urls: list[str],
        cookies_jar_path: Path = Path("state/cookies_jar.json"),
        profile_root: Path = Path("state/session_profiles"),
        forwarder_base_port: int = DEFAULT_FORWARDER_BASE_PORT,
        refresh_interval_per_session_s: float = DEFAULT_REFRESH_INTERVAL_PER_SESSION_S,
        harvest_via_local_ip: bool = False,
        homepage_url: str = HOMEPAGE_URL,
    ):
        # homepage_url — the retailer's homepage that each session's tab
        # parks on after launch and keepalive-renavigates. Defaults to
        # the module-level HOMEPAGE_URL (Target) so existing callers stay
        # unchanged; Walmart's ResilientChecker passes
        # "https://www.walmart.com" via WalmartAdapter.base_url.
        self.homepage_url = homepage_url
        # If harvest_via_local_ip=True, Chromes launch WITHOUT --proxy-server,
        # so the cookie harvest happens on the user's home IP (high trust).
        # Workers would still need to send requests through BD — but in the
        # persistent-Chrome architecture, the worker IS the Chrome, so this
        # flag effectively makes the entire session use the home IP. Only
        # useful if you have ≤10 sessions and want to bypass BD entirely.
        self.proxy_urls = list(proxy_urls)
        self.harvest_via_local_ip = harvest_via_local_ip
        self.cookies_jar_path = Path(cookies_jar_path)
        self.cookies_jar_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.refresh_interval_per_session_s = refresh_interval_per_session_s

        self.sessions: list[SessionEntry] = []
        self.forwarder_pool = ForwarderPool()
        for i, url in enumerate(self.proxy_urls):
            port = forwarder_base_port + i
            up = self.forwarder_pool.add_upstream(url, port)
            session_id = f"s{i+1}"
            profile_dir = self.profile_root / session_id
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.sessions.append(SessionEntry(
                id=session_id,
                proxy_url=url,
                proxy_ip=up.pinned_ip,
                local_port=port,
                profile_dir=profile_dir,
            ))

        self._stop_event = asyncio.Event()
        self._keepalive_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._launch_task: Optional[asyncio.Task] = None

    # ───────── lifecycle ─────────

    async def start(self):
        await self.forwarder_pool.start_all()
        # Block here until ALL Chromes are launched. Mixing ongoing launches
        # with in-progress purchases creates contention (the user observed:
        # 1st purchase fired fine, but the subsequent stream of new-Chrome
        # launches blocked the test-mode purchase loop from re-firing). Wait
        # for the full pool to be ready, then let the dispatcher take over.
        # With CHROME_STAGGER_TOTAL_S=30 the wait is ~30s, not 10min.
        await self._launch_all_persistent()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(),
                                                   name="multi_session_keepalive")
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(),
                                                  name="multi_session_watchdog")
        ready, total = self.session_count()
        logger.info(f"[MULTI_SESSION] started — {ready}/{total} sessions ready, "
                    f"dispatcher cleared to begin sweeping")

    async def stop(self):
        """Tear down with hard time bounds at every step. Without these, a
        zendriver browser.stop() that hangs on a CDP socket OR a forwarder
        server.wait_closed() blocked on an open CONNECT tunnel keeps the bot
        from exiting on Ctrl+C (observed during 60-min stress 2026-05-13)."""
        self._stop_event.set()
        for t in (self._launch_task, self._keepalive_task, self._watchdog_task):
            if t:
                t.cancel()
                try:
                    await asyncio.wait_for(t, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
        # Tear down browsers in parallel — sequential 5s timeouts × N sessions
        # adds up to N×5s worst case. Parallel = max 5s for the whole pool.
        try:
            await asyncio.wait_for(
                asyncio.gather(*(self._teardown_browser(s) for s in self.sessions),
                               return_exceptions=True),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[MULTI_SESSION] browser teardown timed out — "
                           "leaving Chrome processes for OS to reap")
        try:
            await asyncio.wait_for(self.forwarder_pool.stop_all(), timeout=6.0)
        except (asyncio.TimeoutError, Exception):
            pass
        logger.info("[MULTI_SESSION] stopped")

    # ───────── launch ─────────

    async def _launch_all_persistent(self):
        """Launch every session sequentially, staggered over CHROME_STAGGER_TOTAL_S
        seconds with per-launch jitter. Shape sees Chromes appearing as if real
        users opened Target at independent random moments — not a coordinated
        bot army flashing into existence."""
        stagger_total = float(os.environ.get("CHROME_STAGGER_TOTAL_S",
                                             str(DEFAULT_STAGGER_TOTAL_S)))
        n = max(1, len(self.sessions))
        per_session_gap = stagger_total / n
        logger.info(f"[MULTI_SESSION] launching {n} persistent Chromes "
                    f"(stagger={stagger_total:.0f}s, gap≈{per_session_gap:.1f}s/session)")
        for i, s in enumerate(self.sessions):
            await self._launch_persistent_one(s)
            self._write_jar()
            if i < len(self.sessions) - 1:
                gap = per_session_gap * random.uniform(0.7, 1.3)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=gap)
                    return
                except asyncio.TimeoutError:
                    pass

    async def _launch_persistent_one(self, s: SessionEntry):
        """Launch this session's Chrome and park its tab open on target.com.
        Does NOT close the browser. Records cookies and visitor_id, sets
        s.state='ready' on success or 'crashed' on failure."""
        s.state = "starting"
        s.last_refresh_attempt = time.time()
        browser_args = [
            "--window-size=1024,768",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-sync",
            "--no-experiments",
            "--metrics-recording-only",
            # BD ISP proxies hard-block google.com — every Chrome→Google call
            # returns 403 in BD's logs and obscures real Target traffic. These
            # flags suppress the remaining Google chatter not covered by
            # --disable-background-networking (safebrowsing, optimization-hints,
            # translate, media-router, account-consistency, domain-reliability).
            "--safebrowsing-disable-auto-update",
            "--disable-client-side-phishing-detection",
            "--disable-domain-reliability",
            # Chrome honors only the LAST --disable-features flag, so we
            # include zendriver's IsolateOrigins/site-per-process here too —
            # otherwise this overwrites the site-isolation disable.
            "--disable-features=IsolateOrigins,site-per-process,"
            "OptimizationHints,Translate,MediaRouter,DialMediaRouteProvider,"
            "InterestFeedV2,CalculateNativeWinOcclusion,AccountConsistency,"
            "SafeBrowsingEnhancedProtectionMessageInInterstitials",
        ]
        if not self.harvest_via_local_ip:
            browser_args.insert(0, f"--proxy-server=127.0.0.1:{s.local_port}")
        cfg = uc.Config(
            user_data_dir=str(s.profile_dir.resolve()),
            headless=False,
            sandbox=sys.platform != "darwin",
            browser_args=browser_args,
            browser_connection_timeout=1.0,
            browser_connection_max_tries=30,
        )
        try:
            t0 = time.time()
            s.browser = await uc.start(cfg)
            s.tab = await asyncio.wait_for(s.browser.get(self.homepage_url), timeout=30.0)
            await asyncio.sleep(SETTLE_AFTER_NAV_S)
            await self._refresh_cookies_from_tab(s)
            s.last_homepage_nav_at = time.time()
            s.state = "ready"
            s.consecutive_errors = 0
            logger.info(f"[MULTI_SESSION] {s.id} ({s.proxy_ip}) ready "
                        f"cookies={len(s.cookies)} visitor_id={s.visitor_id[:8]}... "
                        f"({time.time()-t0:.1f}s)")
        except Exception as e:
            logger.warning(f"[MULTI_SESSION] {s.id} launch failed: {e}")
            await self._teardown_browser(s)
            s.state = "crashed"
            s.crash_count += 1

    async def _refresh_cookies_from_tab(self, s: SessionEntry):
        """Pull current cookies off the existing tab (no nav). Caller is
        expected to have just navigated the tab if cookie refresh is the goal."""
        if s.tab is None:
            return
        response = await asyncio.wait_for(
            s.tab.send(cdp.network.get_all_cookies()), timeout=5.0
        )
        new_cookies = {c.name: c.value for c in response
                       if c.name in HARVESTER_RELEVANT_COOKIES}
        if new_cookies:
            s.cookies = new_cookies
            s.visitor_id = new_cookies.get("visitorId", s.visitor_id)
            s.harvested_at = time.time()

    # ───────── keepalive ─────────

    async def _keepalive_loop(self):
        """Round-robin: each session re-navigates target.com once per
        refresh_interval_per_session_s. With 22 sessions and 1800s interval,
        gap is ~82s between heartbeats — never bursty, always one Chrome
        active at a time."""
        per_session = self.refresh_interval_per_session_s
        gap = per_session / max(1, len(self.sessions))
        while not self._stop_event.is_set():
            for s in self.sessions:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=gap)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    await self._heartbeat_one(s)
                except Exception as e:
                    logger.warning(f"[MULTI_SESSION] {s.id} heartbeat failed: {e}")
                    s.failed_refreshes += 1

    async def _heartbeat_one(self, s: SessionEntry):
        """Navigate this session's EXISTING tab back to the retailer's homepage,
        let cookies settle, dump them. Browser stays alive throughout.

        Skips sessions with in_queue=True — those are waiting at /qp and
        re-navigating would discard their queue ticket. The queue handler
        is responsible for refreshing cookies on those sessions via the
        queue page's own JS-driven PerimeterX challenges.
        """
        if s.state != "ready" or s.tab is None:
            return
        if s.in_queue:
            logger.debug(
                f"[MULTI_SESSION] {s.id} heartbeat SKIPPED (in_queue=True)"
            )
            return
        async with s.busy_lock:
            # Re-check inside the lock — caller may have set in_queue between
            # the outer check and lock acquisition.
            if s.in_queue:
                return
            s.state = "refreshing"
            try:
                await asyncio.wait_for(s.tab.get(self.homepage_url), timeout=20.0)
                await asyncio.sleep(random.uniform(4.0, 8.0))
                await self._refresh_cookies_from_tab(s)
                s.last_homepage_nav_at = time.time()
                s.state = "ready"
                self._write_jar()
                logger.debug(f"[MULTI_SESSION] {s.id} heartbeat ok "
                             f"cookies={len(s.cookies)}")
            except Exception:
                s.state = "crashed"
                s.consecutive_errors += 1
                raise

    # ───────── crash recovery ─────────

    async def _watchdog_loop(self):
        """Every WATCHDOG_INTERVAL_S, look for sessions in state='crashed' that
        haven't been retried in RECYCLE_COOLDOWN_S, recycle ONE per tick (so
        we don't burst-relaunch the entire pool if everything failed at once)."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=WATCHDOG_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            now = time.time()
            for s in self.sessions:
                if s.state == "crashed" and (now - s.last_refresh_attempt) > RECYCLE_COOLDOWN_S:
                    asyncio.create_task(self._recycle_one(s),
                                        name=f"recycle_{s.id}")
                    break    # one recycle per tick

    async def _recycle_one(self, s: SessionEntry):
        """Tear down a crashed session and relaunch it. Profile dir is
        preserved so cookies survive the recycle."""
        if s.state == "recycling":
            return
        prior_state = s.state
        s.state = "recycling"
        logger.info(f"[MULTI_SESSION] recycling {s.id} (was={prior_state}, "
                    f"crash_count={s.crash_count})")
        await self._teardown_browser(s)
        await asyncio.sleep(random.uniform(5, 15))
        await self._launch_persistent_one(s)

    async def _teardown_browser(self, s: SessionEntry):
        if s.browser is not None:
            try:
                await asyncio.wait_for(s.browser.stop(), timeout=5.0)
            except Exception:
                pass
            s.browser = None
        s.tab = None

    # ───────── persistence ─────────

    def _write_jar(self):
        """Snapshot current cookies + visitor_ids to disk for forensic / restart
        purposes. The on-disk file is NOT load-bearing: live state is in the
        Chrome processes. Atomic write + fsync for power-loss safety."""
        payload = {
            "schema": "multi_session_v2_persistent",
            "sessions": [s.to_dict() for s in self.sessions if s.cookies],
            "updated_at": time.time(),
        }
        tmp = self.cookies_jar_path.with_suffix(f".tmp.{os.getpid()}")
        try:
            data = json.dumps(payload, indent=2)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            os.replace(tmp, self.cookies_jar_path)
        except Exception as e:
            logger.warning(f"[MULTI_SESSION] jar write failed: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass

    # ───────── worker-facing API ─────────

    def pick_session(self) -> Optional[SessionEntry]:
        """Pick a random ready, non-busy session. None if no session is
        currently available (all parked / busy / refreshing / crashed / in queue).

        Note: the legacy Target version of this method also required
        `s.visitor_id` to be set, because RedSky needs a stable visitor
        identity. That check is retailer-specific and was dropped here so
        Walmart (which doesn't issue a `visitorId` cookie) and any future
        retailer can use the same pool unchanged. Retailers that need a
        per-session identity can validate it in their adapter's
        `build_fetch_js` instead.

        Sessions with in_queue=True are also excluded — they're parked at
        /qp holding a queue ticket and shouldn't be used for normal
        stock-check dispatches.
        """
        ready = [s for s in self.sessions
                 if s.state == "ready"
                 and s.tab is not None
                 and not s.in_flight
                 and not s.in_queue
                 and s.cookies]
        if not ready:
            return None
        return random.choice(ready)

    def session_count(self) -> tuple[int, int]:
        """Return (ready_sessions, total_sessions)."""
        ready = sum(1 for s in self.sessions
                    if s.state == "ready" and s.cookies)
        return ready, len(self.sessions)

    def state_summary(self) -> dict[str, int]:
        out = {"starting": 0, "ready": 0, "refreshing": 0,
               "crashed": 0, "recycling": 0}
        for s in self.sessions:
            out[s.state] = out.get(s.state, 0) + 1
        return out

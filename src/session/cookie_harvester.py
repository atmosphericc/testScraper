"""
Cookie harvester — Refract-pattern session anchor.

⚠️  DEPRECATED (2026-05-13): Superseded by the browser-native rearchitect.

In the new architecture, MultiSessionPool maintains N permanent Chrome
instances (one per BD IP); each Chrome owns its own live session and live
cookie jar. There is no longer a single shared cookies_jar.json that workers
read — each session has its own cookies in memory on its own Chrome.

The legacy curl_cffi worker path (which read cookies_jar.json) has been
removed from ResilientStockChecker. This file remains only for emergency
fallback if the legacy path is re-enabled. Schedule deletion after 2 weeks
of stable browser-native operation.

──────────────────────────────────────────────────────────────────────────

One zendriver headed browser maintains a real Target session through the local
forwarder pool (which handles BD upstream auth). On a heartbeat schedule it
navigates back to target.com to refresh PX/Akamai cookies, then atomically
writes the relevant cookies + a generated visitor_id to state/cookies_jar.json.

curl_cffi-based stock workers read that jar on each cycle so their requests
carry the same session-trust signals a real browsing user would have.

Resilience:
  - Browser launches headed (Refract docs require visible window for trust)
  - If browser crashes: auto-relaunch (up to MAX_RETRIES per 5 min)
  - If a navigation fails: retry the heartbeat on next cycle
  - Atomic cookies_jar.json writes — readers never see a half-written file
  - State persisted: relaunches pick up the last-known good cookies
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import zendriver as uc
from zendriver import cdp

from src.proxy.local_forwarder import ForwarderPool, parse_bd_proxy_url

logger = logging.getLogger(__name__)

HARVESTER_RELEVANT_COOKIES = {
    "visitorId", "_px2", "_px3", "_pxvid", "_pxhd", "pxcts",
    "_abck", "bm_sz", "bm_sv", "bm_so", "bm_ni",
    "TealeafAkaSid", "3YCzT93n",
    "_tgt_session", "sapphire", "ffsession",
    "UserLocation", "fiatsCookie", "crl8.fpcuid",
}

DEFAULT_REFRESH_INTERVAL_S = 1200   # 20 min between heartbeats
MAX_RELAUNCH_RETRIES = 3
SETTLE_AFTER_NAV_S = 10
HOMEPAGE_URL = "https://www.target.com"


class CookieHarvester:
    """Background async task: maintain cookies_jar.json from a real Chrome session."""

    def __init__(
        self,
        upstream_proxy_url: Optional[str] = None,
        cookies_jar_path: Path = Path("state/cookies_jar.json"),
        local_forwarder_port: int = 22000,
        refresh_interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
    ):
        self.upstream_proxy_url = upstream_proxy_url
        self.cookies_jar_path = Path(cookies_jar_path)
        self.cookies_jar_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_forwarder_port = local_forwarder_port
        self.refresh_interval_s = refresh_interval_s

        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._browser: Optional[uc.Browser] = None
        self._profile_dir: Optional[Path] = None
        self._forwarder_pool: Optional[ForwarderPool] = None
        self._last_harvest_at: float = 0.0
        self._last_harvest_count: int = 0
        self._relaunch_history: list[float] = []

    # ───────── lifecycle ─────────

    async def start(self):
        self._task = asyncio.create_task(self._main_loop(), name="cookie_harvester")
        logger.info("[HARVESTER] started")

    async def stop(self):
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._cleanup_browser()
        if self._forwarder_pool:
            try:
                await self._forwarder_pool.stop_all()
            except Exception:
                pass
        logger.info("[HARVESTER] stopped")

    # ───────── main loop ─────────

    async def _main_loop(self):
        # Initial harvest
        await self._do_one_harvest()

        # Schedule subsequent refreshes
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.refresh_interval_s,
                )
                return  # stop signaled
            except asyncio.TimeoutError:
                pass

            try:
                await self._do_one_harvest()
            except Exception as e:
                logger.exception(f"[HARVESTER] heartbeat failed: {e}")

    async def _do_one_harvest(self):
        """One harvest cycle: ensure browser is up, navigate, dump cookies."""
        try:
            if self._browser is None:
                await self._launch_browser()

            cookies = await self._navigate_and_dump()
            if cookies:
                self._write_jar(cookies)
                self._last_harvest_at = time.time()
                self._last_harvest_count = len(cookies)
                logger.info(f"[HARVESTER] harvested {len(cookies)} cookies "
                            f"({sorted(cookies.keys())[:5]}...)")
        except Exception as e:
            logger.warning(f"[HARVESTER] harvest cycle error: {e}")
            await self._cleanup_browser()
            await self._maybe_relaunch()

    # ───────── browser plumbing ─────────

    async def _launch_browser(self):
        """Start the forwarder (if proxy configured) + the browser."""
        if self.upstream_proxy_url:
            self._forwarder_pool = ForwarderPool()
            self._forwarder_pool.add_upstream(self.upstream_proxy_url,
                                              self.local_forwarder_port)
            await self._forwarder_pool.start_all()
            proxy_arg = f"--proxy-server=127.0.0.1:{self.local_forwarder_port}"
            host, port, user, pwd, pinned = parse_bd_proxy_url(self.upstream_proxy_url)
            logger.info(f"[HARVESTER] forwarder up — exit IP {pinned}")
        else:
            proxy_arg = None
            logger.info("[HARVESTER] running through home IP (no proxy)")

        profile_dir = Path(tempfile.mkdtemp(prefix="harvester_"))
        self._profile_dir = profile_dir
        args = [
            "--window-size=1280,900",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if proxy_arg:
            args.insert(0, proxy_arg)

        cfg = uc.Config(
            user_data_dir=str(profile_dir),
            headless=False,
            sandbox=sys.platform != "darwin",
            browser_args=args,
            browser_connection_timeout=1.0,
            browser_connection_max_tries=30,
        )
        self._browser = await uc.start(cfg)
        logger.info("[HARVESTER] browser launched")

    async def _cleanup_browser(self):
        if self._browser:
            try:
                await asyncio.wait_for(self._browser.stop(), timeout=5.0)
            except Exception:
                pass
            self._browser = None
        if self._profile_dir:
            try:
                shutil.rmtree(self._profile_dir, ignore_errors=True)
            except Exception:
                pass
            self._profile_dir = None
        if self._forwarder_pool:
            try:
                await self._forwarder_pool.stop_all()
            except Exception:
                pass
            self._forwarder_pool = None

    async def _maybe_relaunch(self):
        """Rate-limit relaunches: max MAX_RELAUNCH_RETRIES in any rolling 5 min."""
        now = time.time()
        self._relaunch_history = [t for t in self._relaunch_history if t > now - 300]
        if len(self._relaunch_history) >= MAX_RELAUNCH_RETRIES:
            logger.error(f"[HARVESTER] too many relaunches ({len(self._relaunch_history)}/5min)"
                         f" — pausing harvester for 5 min")
            await asyncio.sleep(300)
            self._relaunch_history = []
        self._relaunch_history.append(now)

    # ───────── navigation & cookie dump ─────────

    async def _navigate_and_dump(self) -> dict[str, str]:
        if self._browser is None:
            return {}

        tab = self._browser.main_tab
        try:
            tab = await asyncio.wait_for(self._browser.get(HOMEPAGE_URL), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("[HARVESTER] homepage nav timed out")
            raise

        await asyncio.sleep(SETTLE_AFTER_NAV_S)

        # Direct CDP call — bypass the buggy browser.cookies.get_all() wrapper
        try:
            response = await asyncio.wait_for(
                tab.send(cdp.network.get_all_cookies()),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[HARVESTER] cookie fetch timed out")
            raise

        # response is a list[cdp.network.Cookie]
        out = {}
        for c in response:
            if c.name in HARVESTER_RELEVANT_COOKIES:
                out[c.name] = c.value
        return out

    # ───────── persistence ─────────

    def _write_jar(self, cookies: dict[str, str]):
        payload = {
            "cookies": cookies,
            "harvested_at": time.time(),
            "source": "zendriver_target_homepage",
        }
        tmp = self.cookies_jar_path.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.cookies_jar_path)
        except Exception as e:
            logger.warning(f"[HARVESTER] jar write failed: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass

    def stats(self) -> dict:
        return {
            "last_harvest_at": self._last_harvest_at,
            "last_harvest_count": self._last_harvest_count,
            "relaunch_recent": len(self._relaunch_history),
        }

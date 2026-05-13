"""
Multi-session pool — Refract pattern at scale.

To achieve competitive sustained throughput against Target/Shape without
tripping the account-level threshold (observed at ~1.7 RPS / 15 min per
single-account session), we maintain N parallel Chrome sessions. Each
session has:
  - Its own persistent profile directory (separate Chrome user)
  - Its own dedicated BD proxy IP (separate exit IP)
  - Its own visitor_id (Target generates this on first visit)
  - Its own cookie set (PX, Akamai, session tokens)

To Shape, each session appears as a separate user browsing Target — well
within normal-user volume per session. Aggregate stock-check throughput is
N × per-session rate, where per-session rate stays safely below Shape's
threshold.

Harvest strategy: sessions are launched sequentially (not concurrently) to
keep resource use low. Each launch takes 15-20s. Cookies persist via the
profile directory so subsequent launches are warm.

Output: state/cookies_jar.json with an array of session entries:
  {
    "sessions": [
      {"id": "s1", "proxy_ip": "X.X.X.X", "visitor_id": "...",
       "cookies": {...}, "harvested_at": <epoch>, "profile_dir": "..."},
      ...
    ]
  }

Workers pick a random session per request and attach its cookies +
visitor_id to the curl_cffi call.
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

DEFAULT_REFRESH_INTERVAL_PER_SESSION_S = 1800     # each session refreshes every 30 min
SETTLE_AFTER_NAV_S = 8
HOMEPAGE_URL = "https://www.target.com"
DEFAULT_FORWARDER_BASE_PORT = 22000


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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "proxy_ip": self.proxy_ip,
            "visitor_id": self.visitor_id,
            "cookies": self.cookies,
            "harvested_at": self.harvested_at,
        }


class MultiSessionPool:
    """
    Maintains N persistent Chrome sessions for the worker pool to round-robin
    through. Sessions are refreshed on a schedule. Heavy operation (browser
    launch) is sequential, not concurrent, to keep memory usage manageable.
    """

    def __init__(
        self,
        proxy_urls: list[str],
        cookies_jar_path: Path = Path("state/cookies_jar.json"),
        profile_root: Path = Path("state/session_profiles"),
        forwarder_base_port: int = DEFAULT_FORWARDER_BASE_PORT,
        refresh_interval_per_session_s: float = DEFAULT_REFRESH_INTERVAL_PER_SESSION_S,
    ):
        self.proxy_urls = list(proxy_urls)
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
        self._task: Optional[asyncio.Task] = None

    # ───────── lifecycle ─────────

    async def start(self):
        await self.forwarder_pool.start_all()
        # Initial harvest of all sessions (sequential)
        await self._initial_harvest()
        # Background loop for refresh schedule
        self._task = asyncio.create_task(self._refresh_loop(), name="multi_session_refresh")
        logger.info(f"[MULTI_SESSION] started — {len(self.sessions)} sessions")

    async def stop(self):
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self.forwarder_pool.stop_all()
        logger.info("[MULTI_SESSION] stopped")

    # ───────── harvest ─────────

    async def _initial_harvest(self):
        """Launch each session once to bootstrap cookies. Sequential."""
        logger.info(f"[MULTI_SESSION] initial harvest of {len(self.sessions)} sessions (sequential)")
        for s in self.sessions:
            await self._harvest_one(s)
            await asyncio.sleep(random.uniform(2, 5))
        self._write_jar()

    async def _refresh_loop(self):
        """Round-robin refresh: each session refreshes every refresh_interval_per_session_s."""
        # Interval between refresh ticks
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
                    await self._harvest_one(s)
                    self._write_jar()
                except Exception as e:
                    logger.warning(f"[MULTI_SESSION] {s.id} refresh failed: {e}")
                    s.failed_refreshes += 1

    async def _harvest_one(self, s: SessionEntry):
        """Launch a fresh browser pinned to this session's profile + proxy,
        navigate to target.com, dump cookies, close."""
        s.last_refresh_attempt = time.time()
        proxy_arg = f"--proxy-server=127.0.0.1:{s.local_port}"
        cfg = uc.Config(
            user_data_dir=str(s.profile_dir.resolve()),
            headless=False,
            sandbox=sys.platform != "darwin",
            browser_args=[
                proxy_arg,
                "--window-size=1024,768",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            browser_connection_timeout=1.0,
            browser_connection_max_tries=30,
        )

        browser = None
        try:
            t0 = time.time()
            browser = await uc.start(cfg)
            tab = await asyncio.wait_for(browser.get(HOMEPAGE_URL), timeout=30.0)
            await asyncio.sleep(SETTLE_AFTER_NAV_S)
            response = await asyncio.wait_for(
                tab.send(cdp.network.get_all_cookies()), timeout=5.0
            )
            new_cookies = {c.name: c.value for c in response
                           if c.name in HARVESTER_RELEVANT_COOKIES}
            if new_cookies:
                s.cookies = new_cookies
                s.visitor_id = new_cookies.get("visitorId", s.visitor_id)
                s.harvested_at = time.time()
                logger.info(f"[MULTI_SESSION] {s.id} ({s.proxy_ip}): "
                            f"harvested {len(new_cookies)} cookies "
                            f"({time.time()-t0:.1f}s)")
        finally:
            if browser:
                try:
                    await asyncio.wait_for(browser.stop(), timeout=5.0)
                except Exception:
                    pass

    # ───────── persistence ─────────

    def _write_jar(self):
        payload = {
            "schema": "multi_session_v1",
            "sessions": [s.to_dict() for s in self.sessions if s.cookies],
            "updated_at": time.time(),
        }
        tmp = self.cookies_jar_path.with_suffix(f".tmp.{os.getpid()}")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.cookies_jar_path)
        except Exception as e:
            logger.warning(f"[MULTI_SESSION] jar write failed: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass

    # ───────── worker-facing API ─────────

    def pick_session(self) -> Optional[SessionEntry]:
        """Pick a random session with valid cookies. None if no session ready yet."""
        ready = [s for s in self.sessions if s.cookies and s.visitor_id]
        if not ready:
            return None
        return random.choice(ready)

    def session_count(self) -> tuple[int, int]:
        """Return (ready_sessions, total_sessions)."""
        ready = sum(1 for s in self.sessions if s.cookies and s.visitor_id)
        return ready, len(self.sessions)

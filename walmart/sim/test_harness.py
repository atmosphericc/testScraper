"""
Test harness — spawn SimServer + zendriver Chrome configured to route
through it, with self-signed cert errors ignored.

This is the foundation every Chrome-based test in Days 2+ uses. It hides
all the orchestration boilerplate behind a simple async context manager:

    async with WalmartSimHarness() as h:
        await h.tab.get("https://www.walmart.com/ip/12345")
        # ... your assertions ...
        log = h.ctl.get_log()

What it does:
  1. Picks a free port + starts SimServer (mitmproxy subprocess)
  2. Pings the /__sim__/ control plane to confirm sim is ready
  3. Launches zendriver Chrome with:
     - --proxy-server=127.0.0.1:<port>
     - --ignore-certificate-errors (accept mitmproxy self-signed cert)
     - --proxy-bypass-list=<-loopback>   (so the control client still
       reaches sim.local via the proxy itself — see below)
     - A throwaway profile dir per test (clean cookies, no /qp tickets
       persisting)
  4. Exposes:
     - h.sim    — SimServer instance
     - h.ctl    — SimControlClient (scripts subprocess state)
     - h.browser — zendriver Browser
     - h.tab    — main tab (already opened)
  5. On exit: tears Chrome down, stops sim, cleans temp dir
"""

from __future__ import annotations

import asyncio
import logging
import socket
import tempfile
from pathlib import Path
from typing import Optional

from walmart.sim.server import SimControlClient, SimServer


logger = logging.getLogger("walmart.sim.test_harness")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WalmartSimHarness:
    """Async context manager: spawn sim + Chrome, hand out (browser, tab, ctl)."""

    def __init__(
        self,
        port: Optional[int] = None,
        headless: bool = True,
        extra_browser_args: Optional[list[str]] = None,
        chrome_connection_timeout: float = 1.0,
        chrome_max_tries: int = 30,
    ):
        self.port = port or _free_port()
        self.headless = headless
        self._extra_args = extra_browser_args or []
        self._chrome_connection_timeout = chrome_connection_timeout
        self._chrome_max_tries = chrome_max_tries
        self._profile_dir: Optional[Path] = None

        self.sim: Optional[SimServer] = None
        self.ctl: Optional[SimControlClient] = None
        self.browser = None
        self.tab = None

    async def __aenter__(self) -> "WalmartSimHarness":
        # Start sim first so the control plane is up before Chrome connects
        self.sim = SimServer(port=self.port)
        self.sim.start(timeout=15.0)
        self.ctl = self.sim.control()
        # Health check
        ping = self.ctl.ping()
        if not ping.get("ok"):
            raise RuntimeError(f"Sim control ping failed: {ping}")
        logger.info(f"Sim ready on {self.sim.proxy_url}")

        # Throwaway profile dir for clean cookies / no leftover /qp tickets
        self._profile_dir = Path(tempfile.mkdtemp(prefix="walmart_sim_chrome_"))
        logger.info(f"Chrome profile: {self._profile_dir}")

        # Build Chrome args
        # --ignore-certificate-errors lets mitmproxy's self-signed CA work
        # --proxy-server routes ALL Chrome traffic through the sim
        # --no-first-run skips Chrome's welcome screens which can hang headless
        # --disable-features hides some Chrome-internal background fetches
        #   that would otherwise pollute the request log
        browser_args = [
            f"--proxy-server=http://127.0.0.1:{self.port}",
            "--ignore-certificate-errors",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=NetworkServiceInProcess2,OptimizationHints,"
            "ChromeWhatsNewUI,InterestFeedContentSuggestions",
        ] + self._extra_args

        # Lazy-import to keep top-level import light + share with zendriver's
        # own startup overhead
        import zendriver as uc
        config = uc.Config(
            user_data_dir=str(self._profile_dir),
            headless=self.headless,
            browser_args=browser_args,
            browser_connection_timeout=self._chrome_connection_timeout,
            browser_connection_max_tries=self._chrome_max_tries,
        )
        self.browser = await uc.start(config)
        logger.info("Chrome launched")

        # Get the main tab. zendriver's API: browser.main_tab
        self.tab = self.browser.main_tab
        if self.tab is None:
            # Fallback: explicit get("about:blank") opens a tab
            self.tab = await self.browser.get("about:blank")

        # Enable Network domain so QueueHandler's CDP listener can attach
        from zendriver import cdp
        await self.tab.send(cdp.network.enable())
        logger.info("Network domain enabled on tab")

        return self

    async def __aexit__(self, *args):
        # Tear down in reverse order
        if self.browser is not None:
            try:
                await asyncio.wait_for(self.browser.stop(), timeout=5.0)
            except Exception as e:
                logger.warning(f"browser stop failed: {e}")
            self.browser = None
            self.tab = None

        if self.sim is not None:
            self.sim.stop(timeout=3.0)
            self.sim = None

        if self._profile_dir is not None and self._profile_dir.exists():
            import shutil
            shutil.rmtree(self._profile_dir, ignore_errors=True)

        # Drop the lingering control client requests session
        if self.ctl is not None:
            try:
                self.ctl._session.close()
            except Exception:
                pass
            self.ctl = None


# Synchronous helper for tests that don't already have an event loop
def run_with_harness(coro_fn, **harness_kwargs):
    """Run an async test function with a fresh harness.

    Usage:
      async def my_test(h):
          await h.tab.get("https://www.walmart.com/ip/12345")
          assert ...

      run_with_harness(my_test)
    """
    async def _runner():
        async with WalmartSimHarness(**harness_kwargs) as h:
            return await coro_fn(h)

    return asyncio.run(_runner())

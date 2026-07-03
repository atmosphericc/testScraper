"""Worker — owns one browser end-to-end.

A Worker is the unit of "one browser, one session, one Target account, one
PurchaseExecutor." Each Worker owns its own asyncio event loop running on a
dedicated background thread, so dispatch from N Workers doesn't serialize
on a shared loop.

WorkerConfig fields:
    worker_id    — small int, 1 by default. Used in log scoping.
    account_id   — free-form label (e.g. "primary", "alt-1").
    session_path — where this worker's cookies+fingerprint live. Default
                   target.json (single-worker behavior).
    profile_dir  — Chrome user data dir. Default nodriver-profile.

Concurrency model:
    Each Worker lazily spawns a daemon thread running its own event loop on
    first call to `run_async`. The loop captured by SessionManager.initialize()
    is automatically this worker's loop, so SessionManager.submit_async_task
    routes back to it correctly.

    `Worker.run_async(coro)` returns a `concurrent.futures.Future`. Callers
    that need to block on the result use `.result(timeout=...)`. Callers that
    are themselves running on the worker's loop should not use run_async —
    they should `await coro` directly.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

from ..session import PurchaseExecutor, SessionKeepAlive, SessionManager


@dataclass
class WorkerConfig:
    """Per-worker configuration. Defaults match today's single-worker setup."""
    worker_id: int = 1
    account_id: str = "primary"
    session_path: str = "target.json"
    profile_dir: str = "nodriver-profile"
    # Per-account exit proxy for the PURCHASE browser. Starts as the value from
    # config/target_accounts.json (may be a Bright-Data auth URL or a plain
    # host:port, or None = home IP). BulletproofPurchaseManager rewrites a BD
    # auth URL into a local forwarder address (127.0.0.1:port) before
    # build_components, since Chrome can't take inline proxy auth.
    proxy_url: Optional[str] = None
    # IANA timezone for this account's device fingerprint (from accounts.json).
    timezone: Optional[str] = None
    # When True, re-apply the per-account CDP fingerprint (account_identity) on
    # the purchase tab so it matches what the harvester logged in under. Only set
    # for file-driven (multi-account) configs — legacy single-account leaves this
    # False so the established primary fingerprint is never altered.
    apply_fingerprint: bool = False

    def __post_init__(self) -> None:
        # Coerce paths to plain strings so downstream comparisons stay simple.
        self.session_path = str(self.session_path)
        self.profile_dir = str(self.profile_dir)
        if self.proxy_url is not None:
            self.proxy_url = str(self.proxy_url).strip() or None
        if self.timezone is not None:
            self.timezone = str(self.timezone).strip() or None


class Worker:
    """A single browser+session+executor unit with its own event loop.

    Lifecycle:
        w = Worker(cfg)
        w.build_components(...)              # constructs SM/Keepalive/Executor
        fut = w.run_async(coro)              # spins up loop on first call
        fut.result(timeout=...)              # block on result
        w.shutdown()                         # stops the loop+thread
    """

    def __init__(self, cfg: Optional[WorkerConfig] = None) -> None:
        self.cfg: WorkerConfig = cfg or WorkerConfig()
        self.session_manager: Optional[SessionManager] = None
        self.session_keepalive: Optional[SessionKeepAlive] = None
        self.purchase_executor: Optional[PurchaseExecutor] = None
        self._built: bool = False

        # Per-worker loop+thread, spun up lazily on first run_async call.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()

    def build_components(
        self,
        *,
        session_status_callback: Optional[Callable] = None,
        purchase_status_callback: Optional[Callable] = None,
    ) -> None:
        """Construct SessionManager + SessionKeepAlive + PurchaseExecutor.

        Idempotent — returns early if already built.
        """
        if self._built:
            return

        self.session_manager = SessionManager(
            session_path=self.cfg.session_path,
            user_data_dir=self.cfg.profile_dir,
            proxy_url=self.cfg.proxy_url,
            account_id=self.cfg.account_id,
            timezone=self.cfg.timezone,
            apply_fingerprint=self.cfg.apply_fingerprint,
        )

        self.session_keepalive = SessionKeepAlive(
            self.session_manager,
            status_callback=session_status_callback,
        )

        self.purchase_executor = PurchaseExecutor(
            self.session_manager,
            status_callback=purchase_status_callback,
        )

        self._built = True

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """The per-worker event loop, or None if not yet started."""
        return self._loop

    def label(self) -> str:
        """Short tag for logging — `[W1/primary]` style."""
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

    def bind_external_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Adopt an externally-managed loop instead of spawning our own.

        Use this when the worker should run on a loop owned by someone else
        (e.g. Worker 1 reusing the legacy global event loop in app.py so the
        stock monitor's `run_coroutine_threadsafe(..., global_loop)` calls
        keep working at N=1). Must be called before any `run_async`.
        """
        with self._loop_lock:
            if self._loop is not None:
                raise RuntimeError(
                    f"{self.label()}: bind_external_loop called after loop already set"
                )
            self._loop = loop
            self._loop_thread = None  # not owned by us

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start the worker's dedicated loop+thread on first use."""
        if self._loop is not None and self._loop.is_running():
            return self._loop

        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            t = threading.Thread(
                target=_run,
                daemon=True,
                name=f"WorkerLoop-{self.cfg.worker_id}",
            )
            t.start()
            ready.wait()  # ensure asyncio.set_event_loop ran before submission

            self._loop = loop
            self._loop_thread = t
            return loop

    def run_async(self, coro: Coroutine[Any, Any, Any]) -> Future:
        """Submit `coro` to this worker's loop, return a concurrent.futures.Future.

        Lazily starts the loop+thread on first call. Use .result(timeout=...)
        to block on completion.
        """
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def shutdown(self) -> None:
        """Stop the worker's loop and thread. Safe to call if never started.
        Does nothing for an externally-bound loop (we don't own it)."""
        loop = self._loop
        thread = self._loop_thread
        if loop is None or thread is None:
            # Either never started, or borrowed from someone else.
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        thread.join(timeout=2.0)
        self._loop = None
        self._loop_thread = None

"""Worker — owns one browser end-to-end.

A Worker is the unit of "one browser, one session, one Target account, one
PurchaseExecutor." At N=1 (current default) one Worker is created inside
BulletproofPurchaseManager and is functionally identical to the previous
inline session+executor wiring. Phase 6 will introduce a WorkerPool that
constructs N independent Workers, each with its own profile dir, session
file, and (eventually) its own event loop.

Today's Worker is intentionally thin — it bundles configuration and the
three existing components (SessionManager, SessionKeepAlive,
PurchaseExecutor) that BulletproofPurchaseManager already manages, and
exposes them as one cohesive unit. No behavioral change vs. the inline
wiring at bulletproof_purchase_manager.py:232-273.

WorkerConfig fields:
    worker_id    — small int, 1 by default. Used in log scoping and to derive
                   default per-worker file paths in Phase 6.
    account_id   — free-form label (e.g. "primary", "alt-1"). At N=1 stays
                   "primary"; Phase 6 lets the user bind specific accounts.
    session_path — where this worker's cookies+fingerprint live. Default
                   target.json (single-worker behavior).
    profile_dir  — Chrome user data dir. Default nodriver-profile.

Future (Phase 6) additions:
    - own asyncio event loop on a dedicated thread
    - submit_purchase(tcin, ...) returning concurrent.futures.Future
    - is_healthy() / restart() lifecycle hooks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..session import PurchaseExecutor, SessionKeepAlive, SessionManager


@dataclass
class WorkerConfig:
    """Per-worker configuration. Defaults match today's single-worker setup."""
    worker_id: int = 1
    account_id: str = "primary"
    session_path: str = "target.json"
    profile_dir: str = "nodriver-profile"

    def __post_init__(self) -> None:
        # Coerce paths to plain strings so downstream comparisons stay simple.
        self.session_path = str(self.session_path)
        self.profile_dir = str(self.profile_dir)


class Worker:
    """A single browser+session+executor unit. Constructs lazily — call
    `build_components()` after instantiation to wire the three components.

    The split between __init__ and build_components mirrors today's split
    in BulletproofPurchaseManager between __init__ (creates the manager)
    and _initialize_session_system (constructs sm/keepalive/executor). This
    lets BulletproofPurchaseManager check feature flags / circuit breaker
    state before deciding whether to actually build the browser.
    """

    def __init__(self, cfg: Optional[WorkerConfig] = None) -> None:
        self.cfg: WorkerConfig = cfg or WorkerConfig()
        self.session_manager: Optional[SessionManager] = None
        self.session_keepalive: Optional[SessionKeepAlive] = None
        self.purchase_executor: Optional[PurchaseExecutor] = None
        self._built: bool = False

    def build_components(
        self,
        *,
        session_status_callback: Optional[Callable] = None,
        purchase_status_callback: Optional[Callable] = None,
    ) -> None:
        """Construct SessionManager + SessionKeepAlive + PurchaseExecutor.

        Idempotent — returns early if already built. Mirrors the construction
        order in BulletproofPurchaseManager._initialize_session_system to
        guarantee parity.
        """
        if self._built:
            return

        self.session_manager = SessionManager(
            session_path=self.cfg.session_path,
            user_data_dir=self.cfg.profile_dir,
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

    def label(self) -> str:
        """Short tag for logging — `[W1/primary]` style."""
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

"""WorkerPool — manages N independent Workers.

Today (default): the pool holds exactly 1 Worker, behaves identically to
Phase 5's single-Worker setup. Set `TARGET_WORKER_POOL_SIZE` env var to
N > 1 to spawn additional Workers. Each extra Worker gets its own
session file (`target-2.json`, `target-3.json`, ...) and Chrome profile
dir (`nodriver-profile-2/`, `nodriver-profile-3/`, ...). Worker 1 keeps
the legacy `target.json` + `nodriver-profile/` paths so the existing
file is reused without migration.

Dispatch model (kept intentionally simple):
  1. **Sticky TCIN → Worker map** — when a TCIN is dispatched the first
     time it claims a Worker, then every subsequent attempt for that TCIN
     lands on the same Worker. Keeps cookie state, account binding, and
     warmup-tab state coherent for repeat purchases of the same SKU.
  2. **Free-Worker selection** — when a TCIN has no sticky mapping yet,
     `acquire_for_tcin(tcin)` picks the Worker with the fewest sticky
     TCINs, breaking ties by `worker_id`. This balances mappings as the
     SKU set grows.
  3. **No preemption.** Today the executor layer is still serial inside
     a single Worker (one browser → one tab). Multiple TCINs assigned to
     the same Worker still serialize at the executor level. The pool's
     job is to spread distinct SKU families across Workers, not to
     parallelize within a Worker.

The pool deliberately does NOT manage:
  - asyncio loops per Worker (deferred — single shared loop today).
  - submit_purchase futures (the Worker.purchase_executor is still
    awaited directly from the manager's existing thread/loop).
  - health checks / restart (the manager's circuit breaker still runs
    against the single primary Worker).

Phase 6 builds the bookkeeping and the Worker fleet. Hooking the
fleet into the dispatch path so distinct SKUs actually run on distinct
browsers is a follow-up — it requires either per-Worker event loops or
a dispatch queue. For now the pool exposes `primary` (Worker 1) which
matches what BulletproofPurchaseManager already aliases.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Dict, List, Optional

from .worker import Worker, WorkerConfig


def _build_worker_configs(size: int) -> List[WorkerConfig]:
    """Construct N WorkerConfig instances with stable, non-clashing paths.

    Worker 1 reuses the legacy file/dir names so existing setups are
    unaffected. Workers 2..N use suffix-N variants.
    """
    if size < 1:
        raise ValueError(f"WorkerPool size must be >= 1 (got {size})")

    configs: List[WorkerConfig] = [WorkerConfig()]  # Worker 1: defaults
    for i in range(2, size + 1):
        configs.append(WorkerConfig(
            worker_id=i,
            account_id=f"alt-{i - 1}",
            session_path=f"target-{i}.json",
            profile_dir=f"nodriver-profile-{i}",
        ))
    return configs


class WorkerPool:
    """Owns the fleet of Workers used by BulletproofPurchaseManager.

    Lifecycle:
      pool = WorkerPool(size=N)
      pool.build_all(session_status_callback=..., purchase_status_callback=...)
      pool.primary  # alias to Worker 1
      pool.acquire_for_tcin('123')  # returns the Worker bound to that TCIN
    """

    @classmethod
    def from_env(cls) -> "WorkerPool":
        """Construct a pool sized by `TARGET_WORKER_POOL_SIZE` (default 1)."""
        try:
            size = int(os.environ.get("TARGET_WORKER_POOL_SIZE", "1"))
        except ValueError:
            size = 1
        if size < 1:
            size = 1
        return cls(size=size)

    def __init__(self, size: int = 1, configs: Optional[List[WorkerConfig]] = None) -> None:
        if configs is not None:
            if len(configs) < 1:
                raise ValueError("WorkerPool requires at least one WorkerConfig")
            self._configs = list(configs)
        else:
            self._configs = _build_worker_configs(size)

        self._workers: List[Worker] = [Worker(cfg) for cfg in self._configs]
        self._sticky: Dict[str, int] = {}  # tcin -> worker index
        self._lock = threading.Lock()
        self._built = False

    @property
    def size(self) -> int:
        return len(self._workers)

    @property
    def primary(self) -> Worker:
        """Worker 1 — the legacy single-worker. Always exists."""
        return self._workers[0]

    @property
    def workers(self) -> List[Worker]:
        return list(self._workers)

    def build_all(
        self,
        *,
        session_status_callback: Optional[Callable] = None,
        purchase_status_callback: Optional[Callable] = None,
    ) -> None:
        """Construct components for every Worker in the fleet.

        Mirrors Worker.build_components — idempotent, safe to call twice.
        Callbacks are passed to every Worker; consumers wanting
        per-Worker callback identity can call `worker.build_components`
        directly with bespoke callables.
        """
        for w in self._workers:
            w.build_components(
                session_status_callback=session_status_callback,
                purchase_status_callback=purchase_status_callback,
            )
        self._built = True

    def acquire_for_tcin(self, tcin: str) -> Worker:
        """Return the Worker bound to `tcin`, creating a sticky mapping
        if none exists.

        Selection rule for new TCINs:
          - pick the Worker with the fewest sticky TCINs already assigned
          - break ties by lowest worker_id
        """
        with self._lock:
            idx = self._sticky.get(tcin)
            if idx is not None and 0 <= idx < len(self._workers):
                return self._workers[idx]

            counts = [0] * len(self._workers)
            for assigned_idx in self._sticky.values():
                if 0 <= assigned_idx < len(counts):
                    counts[assigned_idx] += 1
            chosen = min(
                range(len(self._workers)),
                key=lambda i: (counts[i], self._workers[i].cfg.worker_id),
            )
            self._sticky[tcin] = chosen
            return self._workers[chosen]

    def release_tcin(self, tcin: str) -> None:
        """Drop the sticky mapping for `tcin`. Safe to call when no mapping exists."""
        with self._lock:
            self._sticky.pop(tcin, None)

    def sticky_assignments(self) -> Dict[str, str]:
        """Snapshot of current TCIN→Worker.label() mappings (for diagnostics)."""
        with self._lock:
            return {
                tcin: self._workers[idx].label()
                for tcin, idx in self._sticky.items()
                if 0 <= idx < len(self._workers)
            }

    def label(self) -> str:
        return f"Pool(size={self.size}, sticky={len(self._sticky)})"

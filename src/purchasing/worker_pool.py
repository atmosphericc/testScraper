"""WorkerPool — manages N independent Workers.

Today (default): the pool holds exactly 1 Worker, behaves identically to
Phase 5's single-Worker setup. Set `TARGET_WORKER_POOL_SIZE` env var to
N > 1 to spawn additional Workers. Each extra Worker gets its own
session file (`target-2.json`, `target-3.json`, ...) and Chrome profile
dir (`nodriver-profile-2/`, `nodriver-profile-3/`, ...). Worker 1 keeps
the legacy `target.json` + `nodriver-profile/` paths so the existing
file is reused without migration.

Dispatch model:
  1. **Sticky TCIN → Worker map** — when a TCIN is dispatched the first
     time it claims a Worker, then every subsequent attempt for that TCIN
     lands on the same Worker. Keeps cookie state, account binding, and
     warmup-tab state coherent for repeat purchases of the same SKU.
  2. **Free-Worker selection** — when a TCIN has no sticky mapping yet,
     `acquire_for_tcin(tcin)` picks the Worker with the fewest sticky
     TCINs, breaking ties by `worker_id`. This balances mappings as the
     SKU set grows.
  3. **Per-Worker event loops** — each Worker owns its own asyncio loop on
     a dedicated thread (see Worker.run_async). Dispatching a purchase to
     a Worker submits the coroutine to that Worker's loop, so N Workers
     can run N concurrent purchases without serializing through a shared
     loop.

The pool exposes `ensure_all_ready()` which launches every Worker's
SessionManager.initialize() concurrently across their per-Worker loops.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

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

    def ensure_all_ready(
        self,
        *,
        per_worker_timeout: float = 90.0,
        warmup_shape_headers: bool = True,
    ) -> Dict[str, Any]:
        """Launch every Worker's browser session concurrently.

        Each Worker submits `session_manager.initialize()` (and optionally
        `purchase_executor.warm_shape_headers()`) to its own loop, so the
        N initializations run in parallel rather than serially.

        Returns a dict per worker label: {ok: bool, init_seconds, warmup_seconds,
        error}. Caller decides what to do with partial failures — typically
        fail-soft (mark worker unhealthy) rather than abort the whole pool.

        Idempotent at the SessionManager level (initialize() short-circuits
        on existing `session_active`), so safe to call again after a partial
        failure.
        """
        if not self._built:
            raise RuntimeError(
                "WorkerPool.ensure_all_ready() called before build_all() — "
                "Workers have no session_manager yet"
            )

        results: Dict[str, Any] = {}

        async def _init_worker(w: Worker) -> Dict[str, Any]:
            t0 = time.time()
            sm = w.session_manager
            ex = w.purchase_executor
            out: Dict[str, Any] = {
                "ok": False,
                "init_seconds": 0.0,
                "warmup_seconds": 0.0,
                "error": None,
            }
            try:
                init_ok = await sm.initialize()
                out["init_seconds"] = round(time.time() - t0, 2)
                if not init_ok:
                    out["error"] = "session_manager.initialize() returned False"
                    return out
                if warmup_shape_headers and ex is not None:
                    w0 = time.time()
                    try:
                        await ex.warm_shape_headers()
                    except Exception as we:
                        out["error"] = f"warm_shape_headers: {we}"
                    out["warmup_seconds"] = round(time.time() - w0, 2)
                out["ok"] = True
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
                out["init_seconds"] = round(time.time() - t0, 2)
                return out

        # Submit each worker's init coroutine to its own loop, then block
        # on all of them. Loops were started lazily inside run_async; if
        # this is the first invocation each worker spins its loop here.
        futures = []
        for w in self._workers:
            fut = w.run_async(_init_worker(w))
            futures.append((w, fut))

        deadline = time.time() + per_worker_timeout * max(1, len(futures))
        for w, fut in futures:
            remaining = max(1.0, deadline - time.time())
            try:
                results[w.label()] = fut.result(timeout=min(remaining, per_worker_timeout))
            except Exception as e:
                results[w.label()] = {
                    "ok": False,
                    "init_seconds": 0.0,
                    "warmup_seconds": 0.0,
                    "error": f"{type(e).__name__}: {e}",
                }

        return results

    def shutdown(self) -> None:
        """Stop every worker's loop+thread. Safe to call if never started."""
        for w in self._workers:
            try:
                w.shutdown()
            except Exception:
                pass

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

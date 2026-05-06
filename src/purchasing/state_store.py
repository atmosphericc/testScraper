"""In-memory authoritative purchase-state store with background disk flush.

Replaces the disk-on-every-read pattern in
`bulletproof_purchase_manager._load_states_unsafe`. The on-disk JSON file
remains the durable representation, but reads are served from memory and
writes are coalesced by a background flusher.

Key APIs:
    - `get_all()` / `get(tcin)` for in-memory reads (microseconds)
    - `update(states_dict)` for bulk replacements (legacy `_save_states_unsafe`
      callers route through this — no behavior change visible)
    - `transition(tcin, expect, to, extras)` atomic CAS for new dispatcher
      logic that wants per-TCIN locking without rescanning the entire dict

Concurrency model:
    Single `RLock` guards the in-memory dict. Workers call methods on the
    store; the store mutates state and signals a `_dirty` event. A background
    flusher thread wakes ~4× per second, takes the lock, snapshots the dict,
    and atomically rewrites the JSON file (write-temp + rename).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


class StateStore:
    """In-memory state with background disk flush. Thread-safe."""

    def __init__(self, state_file: str | Path, flush_interval: float = 0.25) -> None:
        self.state_file = Path(state_file)
        self._flush_interval = flush_interval
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._flush_lock = threading.Lock()  # serialize concurrent flushes
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None
        self._loaded = False
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load_from_disk(self) -> Dict[str, Dict[str, Any]]:
        """One-shot load at startup. Idempotent — subsequent calls return the
        in-memory copy without touching disk."""
        with self._lock:
            if self._loaded:
                return dict(self._states)
            try:
                if self.state_file.exists():
                    with open(self.state_file, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self._states = data
                        else:
                            print(f"[STATE_STORE] Invalid state file format ({type(data).__name__}), resetting")
                            self._states = {}
                else:
                    self._states = {}
            except (json.JSONDecodeError, IOError) as e:
                print(f"[STATE_STORE] Failed to load {self.state_file}: {e}, resetting")
                self._states = {}
            self._loaded = True
            return dict(self._states)

    def start_flush_thread(self) -> None:
        """Spawn the background flush thread. Idempotent."""
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        self._stop.clear()
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="StateStoreFlush",
            daemon=True,
        )
        self._flush_thread.start()

    def shutdown(self, *, timeout: float = 5.0, mark_attempting_as: Optional[str] = None) -> None:
        """Flush any pending writes, optionally rewrite stuck `attempting`/`queued`
        rows, and stop the flush thread."""
        with self._lock:
            if mark_attempting_as is not None:
                now = time.time()
                for tcin, state in self._states.items():
                    if state.get('status') in ('attempting', 'queued'):
                        state['status'] = mark_attempting_as
                        state['final_outcome'] = mark_attempting_as
                        state['shutdown_at'] = now
                self._dirty.set()
        # final synchronous flush
        self._flush_now()
        self._stop.set()
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, tcin: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._states.get(tcin)
            return dict(state) if state is not None else None

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Return a deep-copy snapshot of the full state dict."""
        with self._lock:
            return {k: dict(v) for k, v in self._states.items()}

    def get_status(self, tcin: str) -> str:
        with self._lock:
            return self._states.get(tcin, {}).get('status', 'ready')

    def active_tcins(self) -> Iterable[str]:
        """TCINs currently in `attempting` or `queued` state."""
        with self._lock:
            return [t for t, s in self._states.items()
                    if s.get('status') in ('attempting', 'queued')]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def set(self, tcin: str, state: Dict[str, Any]) -> None:
        """Set the full state dict for one TCIN. Mark dirty."""
        with self._lock:
            self._states[tcin] = dict(state)
            self._dirty.set()

    def update(self, states: Dict[str, Dict[str, Any]]) -> None:
        """Replace the full in-memory dict (bulk update). Legacy
        `_save_states_unsafe(states)` calls route through this for back-compat."""
        with self._lock:
            self._states = {k: dict(v) for k, v in states.items()}
            self._dirty.set()

    def patch(self, tcin: str, **fields: Any) -> Optional[Dict[str, Any]]:
        """Merge `fields` into an existing TCIN state. Returns the updated state
        or None if the TCIN doesn't exist."""
        with self._lock:
            if tcin not in self._states:
                return None
            self._states[tcin].update(fields)
            self._dirty.set()
            return dict(self._states[tcin])

    def remove(self, tcin: str) -> bool:
        with self._lock:
            if tcin in self._states:
                del self._states[tcin]
                self._dirty.set()
                return True
            return False

    def transition(self, tcin: str, *, expect: set[str] | None = None,
                   to: str, extras: Optional[Dict[str, Any]] = None) -> bool:
        """Atomic CAS-style state transition.

        Args:
            tcin: target TCIN.
            expect: set of acceptable current statuses; if the current status is
                NOT in this set, the transition fails. None means "always
                allow".
            to: new status string.
            extras: optional extra fields merged into the state dict on
                success.

        Returns:
            True if transition succeeded, False otherwise.
        """
        with self._lock:
            current = self._states.get(tcin, {'status': 'ready'})
            current_status = current.get('status', 'ready')
            if expect is not None and current_status not in expect:
                return False
            new_state = dict(current)
            new_state['status'] = to
            if extras:
                new_state.update(extras)
            self._states[tcin] = new_state
            self._dirty.set()
            return True

    # ------------------------------------------------------------------
    # Disk flush
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            triggered = self._dirty.wait(timeout=self._flush_interval)
            if self._stop.is_set():
                break
            if not triggered:
                continue
            self._dirty.clear()
            self._flush_now()

    def _flush_now(self) -> bool:
        """Atomic write-temp + rename. Returns True on success.

        Holds `_flush_lock` so concurrent flushes (background + force) don't
        race on the temp file. The temp file embeds thread id so even if the
        lock is contended the temp paths are disjoint.
        """
        with self._flush_lock:
            with self._lock:
                snapshot = {k: dict(v) for k, v in self._states.items()}
            temp_file = f"{self.state_file}.tmp.{os.getpid()}.{threading.get_ident()}"
            try:
                with open(temp_file, 'w') as f:
                    json.dump(snapshot, f, indent=2)
                os.replace(temp_file, self.state_file)
                return True
            except Exception as e:
                print(f"[STATE_STORE] Flush failed: {e}")
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except OSError:
                    pass
                return False

    def force_flush(self) -> bool:
        """Synchronous flush — for tests and shutdown."""
        return self._flush_now()

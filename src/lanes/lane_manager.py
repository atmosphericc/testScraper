"""LaneManager — top-level orchestrator for the independent-lane system.

Manages the full lifecycle: build pool → start harvesters → warmup →
ready for stock flips. Called from app.py when TARGET_LANE_MODE=1.

Usage:
    manager = LaneManager.create(worker_map)
    manager.start()   # starts harvesters, waits for warmup
    manager.on_stock_flip(tcin, qty)   # fans to all lanes
    manager.stop()

The manager also provides a health check for the dashboard and a
warmup wait (harvesters must have tokens before the first race).
"""

from __future__ import annotations

import os
import time
import threading
from typing import Any, Dict, List, Optional

from .lane_pool import LanePool


class LaneManager:
    """Orchestrates the lane pool + harvesters + health checks."""

    def __init__(self, pool: LanePool):
        self.pool = pool
        self._started = False
        self._warmup_done = False
        self._warmup_target = int(os.environ.get('TARGET_LANE_WARMUP_TOKENS', '5'))
        self._warmup_timeout_s = float(os.environ.get('TARGET_LANE_WARMUP_TIMEOUT_S', '120'))

    @classmethod
    def create(cls, worker_map: Optional[Dict[str, Any]] = None,
               claim_registry: Optional[Any] = None) -> 'LaneManager':
        pool = LanePool.from_config(worker_map=worker_map,
                                    claim_registry=claim_registry)
        return cls(pool)

    def start(self, block_for_warmup: bool = True) -> bool:
        """Start harvesters and optionally wait for warmup.

        Returns True if warmup succeeded (all harvesters have tokens).
        """
        if self._started:
            return self._warmup_done

        n_lanes = len(self.pool)
        n_harvesters = len(self.pool.harvesters)
        if n_lanes == 0:
            print("[LANE_MGR] No lanes configured — lane mode inactive")
            return False

        print(f"[LANE_MGR] Starting: {n_lanes} lanes, {n_harvesters} harvesters")
        self.pool.start_harvesters()
        self._started = True

        if block_for_warmup:
            return self._wait_for_warmup()
        return True

    def _wait_for_warmup(self) -> bool:
        """Block until all harvesters have produced at least N tokens."""
        target = self._warmup_target
        timeout = self._warmup_timeout_s
        t0 = time.time()

        print(f"[LANE_MGR] Warming up: waiting for {target} tokens per harvester "
              f"(timeout={timeout}s)")

        while (time.time() - t0) < timeout:
            all_ready = True
            for label, h in self.pool.harvesters.items():
                avail = h.bank.available
                if avail < target:
                    all_ready = False
                    break

            if all_ready:
                elapsed = time.time() - t0
                print(f"[LANE_MGR] Warmup complete in {elapsed:.1f}s")
                for label, h in self.pool.harvesters.items():
                    print(f"  {label}: {h.bank.available} tokens ready")
                self._warmup_done = True
                return True

            time.sleep(2.0)

        # Timeout — report status
        print(f"[LANE_MGR] Warmup TIMEOUT after {timeout}s")
        for label, h in self.pool.harvesters.items():
            avail = h.bank.available
            status = 'READY' if avail >= target else 'LOW'
            print(f"  {label}: {avail} tokens [{status}]")

        # Proceed anyway if at least some harvesters have tokens
        any_ready = any(h.bank.available > 0 for h in self.pool.harvesters.values())
        self._warmup_done = any_ready
        if any_ready:
            print("[LANE_MGR] Partial warmup — proceeding with available harvesters")
        return any_ready

    def on_stock_flip(self, tcin: str, quantity: int = 2) -> List[threading.Thread]:
        """Fan a stock flip to all ready lanes."""
        if not self._started:
            print("[LANE_MGR] Not started — ignoring stock flip")
            return []
        return self.pool.on_stock_flip(tcin, quantity)

    def stop(self) -> None:
        self.pool.stop()
        self._started = False
        self._warmup_done = False
        print("[LANE_MGR] Stopped")

    @property
    def is_ready(self) -> bool:
        return self._started and self._warmup_done

    def health(self) -> Dict[str, Any]:
        """Dashboard health check."""
        harvester_health = {}
        for label, h in self.pool.harvesters.items():
            s = h.status()
            harvester_health[label] = {
                'running': s['running'],
                'chrome': 'UP' if s['chrome_alive'] else 'DOWN',
                'bank': s['bank']['available'],
                'harvested': s['harvests'],
            }

        lane_summary = []
        for lane in self.pool.lanes:
            s = lane.summary()
            lane_summary.append({
                'lane': s['lane'],
                'shots': s['shots'],
                'edge_pass': s['edge_pass'],
                'carts': s['carts'],  # ATC carts, not placed orders (see Lane.summary)
                'racing': s['racing'],
            })

        return {
            'mode': 'lane',
            'started': self._started,
            'ready': self.is_ready,
            'n_lanes': len(self.pool),
            'n_harvesters': len(self.pool.harvesters),
            'harvesters': harvester_health,
            'lanes': lane_summary,
            'checkouts': {
                aid: co.stats()
                for aid, co in self.pool.checkouts.items()
            },
        }

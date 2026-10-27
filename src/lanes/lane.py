"""Lane — one independent ATC firing lane (account × proxy × Shape bank).

Each lane is fully autonomous: it has its own exit IP, its own Shape
token bank (via a shared harvester), its own curl_cffi shooter, and its
own cadence policy.  When the stock monitor detects an in-stock flip,
every lane fires independently — no shared coordination on the ATC step.
From the edge limiter's and Shape's perspective, N lanes = N independent
shoppers.

A Lane wraps:
  - LaneCadence: per-lane state machine (edge 429 -> fast retry, Shape 401 -> cool-off)
  - LaneShooter: curl_cffi ATC POST using Shape tokens from the harvester
  - LaneCheckout: hands 201 carts to the account's Chrome for place_order
  - Worker reference: the account's existing Chrome + session for checkout

The fleet-level dispatch coordination (wave-first sleep, race aggregation)
is replaced by per-lane autonomy.
"""

from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class LaneCadence:
    """Per-lane cadence state — drives independent firing decisions."""

    # Config (set at lane creation, tunable via env)
    fast_retry_min_s: float = 3.0
    fast_retry_max_s: float = 4.5
    cool_401_s: float = 25.0
    cool_401_streak: int = 3

    # Live state
    last_shot_ts: float = 0.0
    consecutive_edge_429: int = 0
    consecutive_shape_401: int = 0
    total_shots: int = 0
    total_edge_pass: int = 0
    total_shape_pass: int = 0
    total_orders: int = 0

    def record_result(self, status: int, body_hint: str = '') -> None:
        """Update cadence state from an ATC result."""
        self.total_shots += 1
        self.last_shot_ts = time.time()

        is_edge_429 = (status == 429 and 'RATE_LIMITED' not in body_hint
                       and 'DCO' not in body_hint)
        is_dco_429 = (status == 429 and ('DCO' in body_hint
                      or 'FAST_SELLING' in body_hint
                      or 'high demand' in body_hint))
        is_shape_401 = (status == 401)
        is_success = (status in (200, 201))

        if is_edge_429 or is_dco_429:
            self.consecutive_edge_429 += 1
            self.consecutive_shape_401 = 0
        elif is_shape_401:
            self.consecutive_shape_401 += 1
            self.consecutive_edge_429 = 0
            self.total_edge_pass += 1
        elif is_success:
            self.consecutive_edge_429 = 0
            self.consecutive_shape_401 = 0
            self.total_edge_pass += 1
            self.total_shape_pass += 1
            self.total_orders += 1
        else:
            self.consecutive_edge_429 = 0
            self.consecutive_shape_401 = 0

    def next_fire_delay(self) -> float:
        """Seconds to wait before the next shot, based on THIS lane's history."""
        import random

        if self.consecutive_shape_401 >= self.cool_401_streak:
            return self.cool_401_s + random.uniform(0, 5)

        return random.uniform(self.fast_retry_min_s, self.fast_retry_max_s)

    def should_fire(self) -> bool:
        """Can this lane fire right now?"""
        if self.last_shot_ts == 0:
            return True
        elapsed = time.time() - self.last_shot_ts
        return elapsed >= self.next_fire_delay()


@dataclass
class Lane:
    """One independent ATC firing lane.

    Fields
    ------
    lane_id : str
        Unique label: '{account_id}@{ip_label}' (e.g. 'primary@31.105.228.245').
    account_id : str
        Which Target account this lane uses.
    proxy_url : str
        The proxy this lane exits through (empty = home IP).
    ip_label : str
        Human-readable IP tag.
    worker : Any
        The Worker object (src/purchasing/worker.py) — for checkout handoff.
    shooter : Any
        LaneShooter instance — fires curl_cffi ATC POSTs.
    checkout : Any
        LaneCheckout instance — hands 201 carts to the account's Chrome.
    cadence : LaneCadence
        This lane's independent cadence state.
    """

    lane_id: str
    account_id: str
    proxy_url: str = ''
    ip_label: str = ''
    worker: Any = None
    shooter: Any = None
    checkout: Any = None
    cadence: LaneCadence = field(default_factory=LaneCadence)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Set by the pool after a stock flip
    _active_tcin: Optional[str] = None
    _race_start: float = 0.0

    @property
    def has_own_ip(self) -> bool:
        return bool(self.proxy_url)

    @property
    def is_home_ip(self) -> bool:
        return not self.proxy_url

    def label(self) -> str:
        if self.ip_label:
            return f"lane:{self.account_id}@{self.ip_label}"
        ip_tag = 'home' if self.is_home_ip else self.proxy_url.split('-ip-')[-1].split(':')[0] if '-ip-' in self.proxy_url else 'proxy'
        return f"lane:{self.account_id}@{ip_tag}"

    def start_race(self, tcin: str) -> None:
        with self._lock:
            self._active_tcin = tcin
            self._race_start = time.time()
            self.cadence.consecutive_edge_429 = 0
            self.cadence.consecutive_shape_401 = 0

    def end_race(self) -> None:
        with self._lock:
            self._active_tcin = None
            self._race_start = 0.0

    @property
    def racing(self) -> bool:
        return self._active_tcin is not None

    def race_elapsed(self) -> float:
        if not self._race_start:
            return 0.0
        return time.time() - self._race_start

    def summary(self) -> Dict[str, Any]:
        c = self.cadence
        shooter_stats = self.shooter.stats() if self.shooter else {}
        return {
            'lane': self.lane_id,
            'account': self.account_id,
            'ip': self.ip_label or ('home' if self.is_home_ip else 'proxy'),
            'shots': c.total_shots,
            'edge_pass': c.total_edge_pass,
            'shape_pass': c.total_shape_pass,
            'orders': c.total_orders,
            'edge_pass_rate': (c.total_edge_pass / c.total_shots * 100
                               if c.total_shots else 0),
            'shape_pass_rate': (c.total_shape_pass / c.total_edge_pass * 100
                                if c.total_edge_pass else 0),
            'no_token': shooter_stats.get('no_token', 0),
            'racing': self.racing,
        }

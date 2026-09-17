#!/usr/bin/env python3
"""Per-(identity, TCIN) shot tracker (2026-09-16 hot-sku 0916 plan P7 DX-1 / P9 ID-1).

Why: the 09-16 forensics found that the Bright Data identity 401 wall tracks
EXPOSURE, meaning how many shots an identity has fired at one hot TCIN in the
current unbroken stretch. Gaps of 87 s or less never reset it; the 121-147 s gaps
that did reset it all coincided with a stock-out. The next audit needs the
exposure of every shot ([EXPOSURE]) and per-identity outcome counters
([IDENT_CENSUS]: P(401), pass per non-401 shot, P(edge429)) instead of
re-deriving them from the logs.

This module is PURE (no I/O, no env reads inside the tracker, its own lock) and
is only the RECORDER. It runs whenever TARGET_EXPOSURE_LOG, TARGET_IDENT_CENSUS
or TARGET_IDENTITY_REST is '1' (see tracker_on()); with all three off the
manager never creates it.

Run model (one "run" per (identity, TCIN)):
  * a recorded shot starts a new run when there is no open run or the gap since
    the run's last shot is >= reset_gap_s (TARGET_IDENTITY_REST_RESET_GAP_S,
    default 120, clamped 30..900);
  * a 'dco' or a 'pass' shot is counted in its run and then CLOSES it (a DCO
    proves auth passed; a pass got through the wall), so the next shot starts
    a new run;
  * run_shots is the 1-based index of the shot in its run; run_s is the time
    since the run's first shot.

Enforcement hook (ID-1, plan P9, stage S6): `_rest_until` is read by
is_resting()/rest_left(). Nothing in this stage writes it, so with the recorder
alone is_resting() is always False.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

# Recorded as 'pass': the ATC got through the edge limiter AND the 401 wall
# (plan P9 record kinds). 'foreign_cart_cleared' / 'cart_qty_stuck' are the other
# two ATC-2xx outcomes the executor can return (added here; see the S3 commit).
PASS_REASONS = frozenset({
    'checkout_busy_retryable', 'checkout_navigation_failed',
    'won_cart_held', 'won_cart_ride_timeout', 'won_cart_retired',
    'cart_qty_cleared', 'cart_qty_stuck', 'foreign_cart_cleared',
})

# Never recorded: nothing reached the carts service on this attempt (or the
# outcome says nothing about the identity's standing on the TCIN).
# 'tcin_throttled_cooldown' is the executor's fire-nothing cooldown bail.
NOT_RECORDED_REASONS = frozenset({
    'cdp_wedged_pre_atc', 'lock_timeout', 'ambiguous_commit_latched',
    'identity_resting', 'home_share_guard', 'account_parked_hot',
    'tcin_throttled_cooldown',
})
NOT_RECORDED_PREFIXES = ('held_cart_',)

# gate_kind values the executor emits (purchase_executor ATC bail branches).
GATE_KINDS = ('auth401', 'edge', 'dco')

COUNTER_KEYS = ('shots', 'p401', 'edge', 'dco', 'pass', 'other')
_KIND_TO_COUNTER = {'auth401': 'p401', 'edge': 'edge', 'dco': 'dco', 'pass': 'pass'}

_FLAG_NAMES = ('TARGET_EXPOSURE_LOG', 'TARGET_IDENT_CENSUS', 'TARGET_IDENTITY_REST')


def _flag(env, name: str) -> bool:
    env = os.environ if env is None else env
    try:
        return str(env.get(name, '0')).strip() == '1'
    except Exception:
        return False


def exposure_log_on(env=None) -> bool:
    """TARGET_EXPOSURE_LOG=1: one [EXPOSURE] line per purchase attempt (default 0)."""
    return _flag(env, 'TARGET_EXPOSURE_LOG')


def ident_census_on(env=None) -> bool:
    """TARGET_IDENT_CENSUS=1: [IDENT_CENSUS] lines when a race finalizes (default 0)."""
    return _flag(env, 'TARGET_IDENT_CENSUS')


def tracker_on(env=None) -> bool:
    """The recorder runs when any consumer is armed (plan P9: 'record always
    runs when REST, CENSUS or EXPOSURE is on'). Default: all off."""
    return any(_flag(env, n) for n in _FLAG_NAMES)


def reset_gap_s(env=None) -> float:
    """TARGET_IDENTITY_REST_RESET_GAP_S (default 120, clamped 30..900)."""
    env = os.environ if env is None else env
    try:
        v = float(str(env.get('TARGET_IDENTITY_REST_RESET_GAP_S', '120')).strip())
    except (TypeError, ValueError):
        v = 120.0
    if not (v == v) or v in (float('inf'), float('-inf')):
        v = 120.0
    return min(900.0, max(30.0, v))


def classify_result(result) -> Optional[str]:
    """Record kind for one executor result: 'pass', a gate_kind ('auth401' /
    'edge' / 'dco'), 'other', or None (not recorded). Never raises."""
    try:
        if not isinstance(result, dict):
            return 'other'
        if result.get('success'):
            return 'pass'
        reason = str(result.get('reason') or '').strip().lower()
        if reason in NOT_RECORDED_REASONS or reason.startswith(NOT_RECORDED_PREFIXES):
            return None
        if reason in PASS_REASONS:
            return 'pass'
        gk = str(result.get('gate_kind') or '').strip().lower()
        if gk in GATE_KINDS:
            return gk
        return 'other'
    except Exception:
        return 'other'


def format_census(ident, tcin, counters: Optional[Dict[str, Any]]) -> str:
    """[IDENT_CENSUS] line for one (identity, TCIN). pass_per_non401 = pass /
    (shots - p401), '-' when there is no non-401 shot. Never raises."""
    try:
        c = {k: int((counters or {}).get(k, 0) or 0) for k in COUNTER_KEYS}
    except Exception:
        c = {k: 0 for k in COUNTER_KEYS}
    non401 = c['shots'] - c['p401']
    ppn = f"{c['pass'] / non401:.3f}" if non401 > 0 else '-'
    return (f"[IDENT_CENSUS] ident={ident} tcin={tcin} shots={c['shots']} "
            f"p401={c['p401']} edge={c['edge']} dco={c['dco']} pass={c['pass']} "
            f"other={c['other']} pass_per_non401={ppn}")


class IdentityTracker:
    """Thread-safe per-(identity, TCIN) run + counter store (see module doc)."""

    RUN_KINDS_MAXLEN = 8

    def __init__(self, reset_gap: float = 120.0):
        self.reset_gap_s = float(reset_gap)
        self._lock = threading.Lock()
        self._runs: Dict[tuple, Dict[str, Any]] = {}
        self._counts: Dict[tuple, Dict[str, Any]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        # ID-1 enforcement hook (stage S6 writes it; the recorder never does).
        self._rest_until: Dict[tuple, float] = {}

    @classmethod
    def from_env(cls, env=None) -> "IdentityTracker":
        return cls(reset_gap=reset_gap_s(env))

    @staticmethod
    def _key(ident, tcin) -> tuple:
        return (str(ident), str(tcin))

    def record(self, ident, acct, tcin, kind: str, now: Optional[float] = None,
               proxied: Optional[bool] = None) -> Dict[str, Any]:
        """Count one shot and return its exposure {'run_shots', 'run_s', 'kind'}."""
        now = time.time() if now is None else float(now)
        kind = str(kind or 'other')
        key = self._key(ident, tcin)
        with self._lock:
            r = self._runs.get(key)
            if (r is None or r.get('closed')
                    or now - float(r.get('last', 0.0)) >= self.reset_gap_s):
                r = {'start': now, 'last': now, 'shots': 0, 'closed': False,
                     'kinds': deque(maxlen=self.RUN_KINDS_MAXLEN)}
                self._runs[key] = r
            r['shots'] += 1
            r['last'] = now
            r['kinds'].append(kind)
            snap = {'run_shots': r['shots'], 'run_s': max(0.0, now - r['start']), 'kind': kind}
            if kind in ('dco', 'pass'):
                r['closed'] = True
            c = self._counts.get(key)
            if c is None:
                c = {k: 0 for k in COUNTER_KEYS}
                c['first'] = now
                self._counts[key] = c
            c['shots'] += 1
            c[_KIND_TO_COUNTER.get(kind, 'other')] += 1
            c['last'] = now
            m = self._meta.setdefault(str(ident), {})
            if acct:
                m['acct'] = str(acct)
            if proxied is not None:
                m['proxied'] = bool(proxied)
        return snap

    def exposure(self, ident, tcin, now: Optional[float] = None) -> Dict[str, Any]:
        """Exposure of the NEXT shot's run without recording anything:
        {'run_shots': shots so far in the open run (0 = none), 'run_s'}."""
        now = time.time() if now is None else float(now)
        with self._lock:
            r = self._runs.get(self._key(ident, tcin))
            if (r is None or r.get('closed')
                    or now - float(r.get('last', 0.0)) >= self.reset_gap_s):
                return {'run_shots': 0, 'run_s': 0.0, 'kind': None}
            return {'run_shots': r['shots'], 'run_s': max(0.0, now - r['start']), 'kind': None}

    def run_kinds(self, ident, tcin) -> list:
        """Kinds of the last shots of the open run (oldest first)."""
        with self._lock:
            r = self._runs.get(self._key(ident, tcin))
            return list(r['kinds']) if r else []

    def counters(self, ident, tcin) -> Dict[str, Any]:
        """Copy of the cumulative counters (all COUNTER_KEYS present)."""
        with self._lock:
            c = self._counts.get(self._key(ident, tcin))
            out = {k: 0 for k in COUNTER_KEYS}
            if c:
                out.update(c)
            return out

    def is_resting(self, ident, tcin, now: Optional[float] = None) -> bool:
        return self.rest_left(ident, tcin, now) > 0.0

    def rest_left(self, ident, tcin, now: Optional[float] = None) -> float:
        now = time.time() if now is None else float(now)
        with self._lock:
            return max(0.0, float(self._rest_until.get(self._key(ident, tcin), 0.0)) - now)

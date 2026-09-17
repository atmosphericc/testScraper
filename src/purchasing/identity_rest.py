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

Enforcement (ID-1, plan P9; built in review round R1, 2026-09-17, NOT armed):
with TARGET_IDENTITY_REST=1 the tracker is built with a rest config and
record() starts a rest when K of the last M shots of an identity's open run on
a TCIN are 'auth401' (default 2 of 3), for eligible identities only (proxied,
unless TARGET_IDENTITY_REST_PROXIED_ONLY=0, and never an account listed in
TARGET_IDENTITY_REST_NEVER, default 'primary'). A rest lasts
TARGET_IDENTITY_REST_S (150, clamped 125..900 so the next shot always opens a
new run) and closes the run. With TARGET_IDENTITY_REST_STAGGER=1 (default) a
second identity's trigger on the same TCIN is deferred while another identity
rests there. The manager enforces it (in-thread skip 'identity_resting' and
the wave-first break). Default '0' = recorder only, is_resting() always False.

HS-1 (plan P5 option a; built in R1, NOT armed): HomeShareGuard parks the GUEST
account (alt-1) on every TCIN for TTL once the PROTECT account (primary)
collects P401 carts-401s / PX-block ATC 403s on one TCIN within 30 min. Only
meaningful when both accounts share the home exit. Default off.
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
# R2 review (R2-DX-HELD-PASS, 2026-09-17): a held-cart re-entry fires checkout
# tickets only, never an add-to-cart, and returns the loop's own result
# (won_cart_held, checkout_busy_retryable, won_cart_retired, even a placed
# order). The executor tags every such result woncart_entry='held'; it is not a
# shot, so it is never recorded (it would count as a pass and close the run).
NOT_RECORDED_ENTRIES = frozenset({'held'})

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


def identity_rest_on(env=None) -> bool:
    """TARGET_IDENTITY_REST=1: ID-1 enforcement (default 0 = recorder only)."""
    return _flag(env, 'TARGET_IDENTITY_REST')


def _num(env, name: str, default: float, lo: float, hi: float) -> float:
    env = os.environ if env is None else env
    try:
        v = float(str(env.get(name, str(default))).strip())
    except (TypeError, ValueError):
        v = float(default)
    if not (v == v) or v in (float('inf'), float('-inf')):
        v = float(default)
    return float(min(float(hi), max(float(lo), v)))


def _names(raw) -> frozenset:
    """'Primary, business;x' -> frozenset({'primary', 'business', 'x'})."""
    out = set()
    for part in str(raw or '').replace(';', ',').replace(' ', ',').split(','):
        part = part.strip().lower()
        if part:
            out.add(part)
    return frozenset(out)


def rest_cfg(env=None) -> Dict[str, Any]:
    """ID-1 knobs, clamped. rest_s >= 125 keeps a rest longer than any reset
    gap the 401 wall was seen to survive (<= 87 s) and past the default run
    reset (120 s)."""
    env = os.environ if env is None else env
    m = int(_num(env, 'TARGET_IDENTITY_REST_M', 3, 1, IdentityTracker.RUN_KINDS_MAXLEN))
    k = int(_num(env, 'TARGET_IDENTITY_REST_K', 2, 1, m))
    return {
        'rest_s': _num(env, 'TARGET_IDENTITY_REST_S', 150, 125, 900),
        'k': k,
        'm': m,
        'proxied_only': str(env.get('TARGET_IDENTITY_REST_PROXIED_ONLY', '1')).strip() != '0',
        'stagger': str(env.get('TARGET_IDENTITY_REST_STAGGER', '1')).strip() != '0',
        'never': _names(env.get('TARGET_IDENTITY_REST_NEVER', 'primary')),
    }


def home_share_guard_on(env=None) -> bool:
    """TARGET_HOME_SHARE_GUARD=1: HS-1 (default 0)."""
    return _flag(env, 'TARGET_HOME_SHARE_GUARD')


def classify_result(result) -> Optional[str]:
    """Record kind for one executor result: 'pass', a gate_kind ('auth401' /
    'edge' / 'dco'), 'other', or None (not recorded). Never raises."""
    try:
        if not isinstance(result, dict):
            return 'other'
        if str(result.get('woncart_entry') or '').strip().lower() in NOT_RECORDED_ENTRIES:
            return None
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

    def __init__(self, reset_gap: float = 120.0, rest: Optional[Dict[str, Any]] = None):
        self.reset_gap_s = float(reset_gap)
        self._lock = threading.Lock()
        self._runs: Dict[tuple, Dict[str, Any]] = {}
        self._counts: Dict[tuple, Dict[str, Any]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        # ID-1 enforcement: None = recorder only (rests never start).
        self.rest = dict(rest) if rest else None
        self._rest_until: Dict[tuple, float] = {}
        self._rest_started: Dict[tuple, float] = {}

    @classmethod
    def from_env(cls, env=None) -> "IdentityTracker":
        return cls(reset_gap=reset_gap_s(env),
                   rest=rest_cfg(env) if identity_rest_on(env) else None)

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
            if self.rest is not None and kind == 'auth401':
                rest = self._rest_trigger_locked(key, r, acct, proxied, now)
                if rest is not None:
                    snap['rest'] = rest
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

    def _rest_trigger_locked(self, key, r, acct, proxied, now) -> Optional[Dict[str, Any]]:
        """ID-1 trigger check for one recorded auth401 (caller holds the lock).
        Returns None (no trigger), {'deferred': True, 'by': ident, ...} or
        {'until', 'rest_s', 'trigger', 'run_shots', 'run_s'}."""
        cfg = self.rest or {}
        k, m = int(cfg.get('k', 2)), int(cfg.get('m', 3))
        recent = list(r['kinds'])[-m:]
        if recent.count('auth401') < k:
            return None
        if str(acct or '').strip().lower() in cfg.get('never', frozenset()):
            return None
        if cfg.get('proxied_only', True) and proxied is not True:
            return None
        if float(self._rest_until.get(key, 0.0)) > now:
            return None
        trigger = f"{k}of{m}"
        if cfg.get('stagger', True):
            for (oi, ot), until in self._rest_until.items():
                if ot == key[1] and oi != key[0] and float(until) > now:
                    return {'deferred': True, 'by': oi, 'trigger': trigger,
                            'left': float(until) - now}
        rest_s = float(cfg.get('rest_s', 150.0))
        self._rest_until[key] = now + rest_s
        self._rest_started[key] = now
        snap = {'until': now + rest_s, 'rest_s': rest_s, 'trigger': trigger,
                'run_shots': r['shots'], 'run_s': max(0.0, now - r['start'])}
        r['closed'] = True       # the next shot after the rest opens a new run
        return snap

    def pop_expired(self, now: Optional[float] = None) -> list:
        """[(ident, tcin, rested_s)] for every rest that has ended since the
        last call; each ended rest is reported exactly once."""
        now = time.time() if now is None else float(now)
        out = []
        with self._lock:
            for key, until in list(self._rest_until.items()):
                if float(until) <= now:
                    started = float(self._rest_started.pop(key, until))
                    self._rest_until.pop(key, None)
                    out.append((key[0], key[1], max(0.0, now - started)))
        return out

    def is_resting(self, ident, tcin, now: Optional[float] = None) -> bool:
        return self.rest_left(ident, tcin, now) > 0.0

    def rest_left(self, ident, tcin, now: Optional[float] = None) -> float:
        now = time.time() if now is None else float(now)
        with self._lock:
            return max(0.0, float(self._rest_until.get(self._key(ident, tcin), 0.0)) - now)


class HomeShareGuard:
    """HS-1 (plan P5 option a; default off, NOT armed): while the GUEST account
    shares the PROTECT account's home exit, PROTECT's carts-401s and PX-block
    ATC 403s on one TCIN within WINDOW_S are the in-drop signal that the extra
    volume is hurting the only converting identity. At P401 of them the GUEST
    sits out every TCIN for TTL (reason 'home_share_guard'); PROTECT is never
    parked. Pure and thread-safe (own lock, no env reads outside from_env)."""

    WINDOW_S = 1800.0

    def __init__(self, guest: str = 'alt-1', protect: str = 'primary',
                 p401: int = 2, ttl_s: float = 3600.0):
        self.guest = str(guest or '').strip().lower()
        self.protect = str(protect or '').strip().lower()
        self.p401 = max(1, int(p401))
        self.ttl_s = float(ttl_s)
        self._lock = threading.Lock()
        self._events: Dict[str, deque] = {}
        self._parked_until = 0.0

    @classmethod
    def from_env(cls, env=None) -> "HomeShareGuard":
        env = os.environ if env is None else env
        return cls(guest=str(env.get('TARGET_HOME_SHARE_GUARD_GUEST', 'alt-1')),
                   protect=str(env.get('TARGET_HOME_SHARE_GUARD_PROTECT', 'primary')),
                   p401=int(_num(env, 'TARGET_HOME_SHARE_GUARD_P401', 2, 1, 50)),
                   ttl_s=_num(env, 'TARGET_HOME_SHARE_GUARD_TTL_S', 3600, 60, 86400))

    def is_protect(self, acct) -> bool:
        return bool(self.protect) and str(acct or '').strip().lower() == self.protect

    def note(self, acct, tcin, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Count one PROTECT 401 / PX-block 403 on `tcin`. Returns the trigger
        {'tcin', 'count', 'until', 'ttl_s'} when this event parks the guest,
        else None. Events of any other account are ignored."""
        if not self.is_protect(acct):
            return None
        now = time.time() if now is None else float(now)
        t = str(tcin)
        with self._lock:
            dq = self._events.setdefault(t, deque())
            while dq and now - dq[0] > self.WINDOW_S:
                dq.popleft()
            dq.append(now)
            if len(dq) < self.p401:
                return None
            count = len(dq)
            dq.clear()
            self._parked_until = max(self._parked_until, now + self.ttl_s)
            return {'tcin': t, 'count': count, 'until': self._parked_until, 'ttl_s': self.ttl_s}

    def guest_left(self, acct, now: Optional[float] = None) -> float:
        """Seconds the account still sits out (0.0 for anyone but the guest,
        and always 0.0 for PROTECT, even when misconfigured as the guest)."""
        a = str(acct or '').strip().lower()
        if not a or a != self.guest or a == self.protect:
            return 0.0
        now = time.time() if now is None else float(now)
        with self._lock:
            return max(0.0, self._parked_until - now)

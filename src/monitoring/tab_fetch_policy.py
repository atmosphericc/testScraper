"""Trusted-browser RedSky fallback policy — ``RESILIENT_FORCE_TAB_FETCH``.

2026-09-07. The 09-04 emergency flag (``=1``) made the stock thread read
RedSky through a live purchase worker's account browser every 4-8 s, in
ADDITION to the resilient BD pool. That was the only detector while the pool
was captcha-walled, but with a healthy pool it only adds CDP contention on the
primary account's tab (09-04..09-07: 30,012 ``get_page`` 3-s timeouts, 4,567
"no result" reads, 0 useful reads beyond what the pool would give).

Modes:
  ``0`` / ``false`` (code default) — never; exact pre-09-04 behaviour.
  ``1`` / ``true``                 — always (09-04 emergency behaviour).
  ``auto``                         — only while the pool is BLIND: no RedSky 200
                                     for ``RESILIENT_TAB_FETCH_BLIND_S`` seconds
                                     (measured from the pool's last 200, or from
                                     its start if it never read one). Switches
                                     itself off on the pool's next 200.

Pure function + a thin ``decide(checker)`` wrapper so the policy is
unit-testable without importing app.py.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

MODE_ENV = 'RESILIENT_FORCE_TAB_FETCH'
BLIND_ENV = 'RESILIENT_TAB_FETCH_BLIND_S'
DEFAULT_BLIND_S = 180.0
_MIN_BLIND_S = 10.0

_ON = ('1', 'true', 'yes', 'on', 'always')
_OFF = ('0', 'false', 'no', 'off', 'never', '')


def mode() -> str:
    return (os.environ.get(MODE_ENV, '0') or '0').strip().lower()


def blind_seconds() -> float:
    try:
        return max(_MIN_BLIND_S, float(os.environ.get(BLIND_ENV, str(DEFAULT_BLIND_S))))
    except (TypeError, ValueError):
        return DEFAULT_BLIND_S


def tab_fetch_wanted(mode_value: Optional[str], last_200_at: Any, started_at: Any,
                     now: Optional[float] = None, blind_s: float = DEFAULT_BLIND_S) -> bool:
    """Decide whether the trusted-browser read should run this cycle."""
    m = (mode_value or '0').strip().lower()
    if m in _ON:
        return True
    if m in _OFF:
        return False
    # auto (any other value): fallback only while the pool is blind.
    try:
        started = float(started_at or 0.0)
        last_ok = float(last_200_at or 0.0)
    except (TypeError, ValueError):
        return False
    if started <= 0.0:
        return False            # pool not started yet — let it boot
    t = time.time() if now is None else float(now)
    return (t - max(started, last_ok)) >= float(blind_s)


def decide(checker: Any, now: Optional[float] = None) -> bool:
    """``tab_fetch_wanted`` fed from a live ResilientStockChecker (or None)."""
    m = mode()
    if m in _ON:
        return True
    if m in _OFF:
        return False
    if checker is None:
        return False
    return tab_fetch_wanted(m,
                            getattr(checker, '_last_200_at', 0.0),
                            getattr(checker, '_start_time', 0.0),
                            now, blind_seconds())

#!/usr/bin/env python3
"""Smoke test: sentinel dead-session park (2026-08-02, 07-31→08-02 audit).

With a dead login-session and the destructive relogin capped, the sentinel
ladder used to run nav-refresh → Chrome RESTART → capped-relogin-no-op every
5-min tick (576 restarts on 08-02 alone), hammering Target's login surface in
the process. The park short-circuits the heavy rungs; only the cheap rung-0
token check keeps watching for recovery (e.g. a manual login).

Pins:
  - capped relogin ⇒ park set; next tick skips nav/restart/relogin entirely
  - failed relogin ⇒ park set
  - token recovery during the park ⇒ True returned, park cleared
  - successful relogin clears any park
  - TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S=0 disables parking (ladder every tick)

No browser, no network. Run: python tests/test_dead_session_park_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.session_manager import SessionManager  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


class _Counters:
    def __init__(self):
        self.token = 0
        self.nav = 0
        self.restart = 0
        self.relogin = 0


def _rig(sm, c, token_ok=False, relogin_allowed=False, relogin_ok=False):
    """Stub the ladder rungs with counters."""
    async def _token():
        c.token += 1
        return token_ok

    async def _nav():
        c.nav += 1
        return False

    async def _restart(guard=False):
        c.restart += 1
        return False

    async def _relogin():
        c.relogin += 1
        return relogin_ok

    async def _save():
        return True

    sm.ensure_fresh_access_token = _token
    sm._trigger_token_refresh = _nav
    sm.refresh_session = _restart
    sm._credential_relogin = _relogin
    sm.save_session_state = _save
    sm._credential_relogin_allowed = lambda: relogin_allowed
    return sm


def _mk():
    return SessionManager(session_path="t.json", account_id="primary")


def test_capped_relogin_parks_and_next_tick_skips_ladder():
    os.environ['TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S'] = '1800'
    c = _Counters()
    sm = _rig(_mk(), c, token_ok=False, relogin_allowed=False)
    ok1 = asyncio.run(sm.ensure_logged_in())
    check("capped: first tick walks ladder, returns False",
          ok1 is False and c.restart == 1 and c.relogin == 0)
    check("capped: park set", sm._dead_session_parked_until > time.time())
    nav_before, restart_before = c.nav, c.restart
    ok2 = asyncio.run(sm.ensure_logged_in())
    check("parked: second tick skips nav/restart/relogin",
          ok2 is False and c.nav == nav_before and c.restart == restart_before
          and c.relogin == 0)


def test_recovery_during_park_clears_it():
    os.environ['TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S'] = '1800'
    c = _Counters()
    sm = _rig(_mk(), c, token_ok=True)
    sm._dead_session_parked_until = time.time() + 900
    ok = asyncio.run(sm.ensure_logged_in())
    check("recovery during park returns True",
          ok is True and c.token == 1 and c.restart == 0)
    check("recovery clears park", sm._dead_session_parked_until == 0.0)


def test_failed_relogin_parks():
    os.environ['TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S'] = '1800'
    c = _Counters()
    sm = _rig(_mk(), c, token_ok=False, relogin_allowed=True, relogin_ok=False)
    ok = asyncio.run(sm.ensure_logged_in())
    check("failed relogin returns False and parks",
          ok is False and c.relogin == 1
          and sm._dead_session_parked_until > time.time())


def test_successful_relogin_clears_park():
    os.environ['TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S'] = '1800'
    c = _Counters()
    sm = _rig(_mk(), c, token_ok=False, relogin_allowed=True, relogin_ok=True)
    sm._dead_session_parked_until = 1.0  # stale park from a prior life
    ok = asyncio.run(sm.ensure_logged_in())
    check("successful relogin returns True and clears park",
          ok is True and sm._dead_session_parked_until == 0.0)


def test_kill_switch_disables_park():
    os.environ['TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S'] = '0'
    try:
        c = _Counters()
        sm = _rig(_mk(), c, token_ok=False, relogin_allowed=False)
        asyncio.run(sm.ensure_logged_in())
        check("kill-switch: no park set", sm._dead_session_parked_until == 0.0)
        asyncio.run(sm.ensure_logged_in())
        check("kill-switch: ladder walks every tick", c.restart == 2)
    finally:
        os.environ['TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S'] = '1800'


if __name__ == '__main__':
    test_capped_relogin_parks_and_next_tick_skips_ladder()
    test_recovery_during_park_clears_it()
    test_failed_relogin_parks()
    test_successful_relogin_clears_park()
    test_kill_switch_disables_park()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

#!/usr/bin/env python3
"""Offline test: the armed KEEPFRESH=0 / RELOGIN_MAX_PER_6H=0 combination, behaviourally (1019b, pins lane).

WHY: the bat arms TARGET_TOKEN_KEEPFRESH=0 and TARGET_RELOGIN_MAX_PER_6H=0 (09-28; the RUNNING bot re-mints a
member token at expiry instead; C-0929-01), and C-1018-16 REFUTED the claim that the purchase_executor
mid-window "dead-token repair" is inert because it is gated on a confirmed 401: the 3x re-probe confirms on
keyless Shape 401s too. Only KEEPFRESH=0 keeps that repair inert. Nothing pinned that in behaviour: the manifest
pins the bat's text, this file pins what the code DOES under those two values.

What is real here: SessionManager.ensure_fresh_access_token / _credential_relogin_allowed / ensure_logged_in and
PurchaseExecutor._refresh_on_tab (the heartbeat -> _confirm_write_auth_401 -> repair branch). What is stubbed:
everything that would touch a browser or the network (refresh_access_token, get_page, _trigger_token_refresh,
refresh_session, _px_challenge_parks_ladder, _credential_relogin, the warmup tab). Each assertion has a mutation
case in this file (the variable set to 1 / a non-zero budget) that flips it, so the pin demonstrably bites.

Run: python tests/test_keepfresh_off_1019.py   (listed in tests/run_offline_suite.py)
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TARGET_API_CAPTURE_CHECKOUT_STEPS"] = "false"     # no file writes in tests
os.environ["TARGET_API_CAPTURE_PLACE_ORDER"] = "false"

import src.session.session_manager as sm_mod  # noqa: E402
import src.session.purchase_executor as pe  # noqa: E402

PASSED: list = []
FAILED: list = []
ARMED = {"TARGET_TOKEN_KEEPFRESH": "0", "TARGET_RELOGIN_MAX_PER_6H": "0"}
_REAL_SLEEP = asyncio.sleep


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


@contextlib.contextmanager
def env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def fast_sleep():
    """The ladder's `await asyncio.sleep(2)` and the confirm path's 0.6 s settles cost no wall time."""
    async def _fast(s, *a, **k):
        await _REAL_SLEEP(0)
    asyncio.sleep = _fast
    try:
        yield
    finally:
        asyncio.sleep = _REAL_SLEEP


def run(coro):
    return asyncio.run(coro)


class Tab:
    """Stub warmup/page tab: no browser. `hb` is the heartbeat dummy POST's status."""
    def __init__(self, hb=401):
        self.url = "https://www.target.com/cart"
        self.hb = hb
        self.evals = 0
        self.gets = []
        self.ex = None

    async def get(self, url):
        self.gets.append(url)

    async def evaluate(self, script, await_promise=False, **kw):
        self.evals += 1
        if "readyState" in script:
            return "complete"
        if self.ex is not None:
            self.ex._cached_cart_headers_ts = time.time()      # the interceptor's header capture
        return self.hb


def make_sm(refresh_result=True, acct="primary"):
    """A bare REAL SessionManager (no __init__: that creates a profile dir) with only the state the
    exercised methods read, and counting stubs for every method that would reach a browser."""
    sm = object.__new__(sm_mod.SessionManager)
    sm.account_id = acct
    sm.logger = logging.getLogger("keepfresh_off_1019")
    sm.logger.addHandler(logging.NullHandler())
    sm.logger.propagate = False
    sm.proxy_url = None
    sm.browser = object()
    sm.purchase_in_progress = False
    sm._relogin_attempt_times = []
    sm._relogin_capped_alerted = False
    sm._last_token_repair_ts = 0.0
    sm._dead_session_parked_until = 0.0
    sm._browser_launched_at = 0.0
    sm._proactive_relaunch_at = 0.0
    sm._age_relaunch_deferred_at = 0.0
    sm.calls = {"refresh_access_token": 0, "get_page": 0, "status": 0, "trigger": 0, "px": 0,
                "refresh_session": 0, "credential_relogin": 0, "save": 0}
    sm.alerts = []
    sm.page = Tab()

    async def refresh_access_token(tab=None, allow_nav=True):
        sm.calls["refresh_access_token"] += 1
        return refresh_result

    async def get_page():
        sm.calls["get_page"] += 1
        return sm.page

    async def get_access_token_status(tab):
        sm.calls["status"] += 1
        return {"present": True, "member": True, "ttl_s": 7200.0}

    async def trigger():
        sm.calls["trigger"] += 1
        return False

    async def px():
        sm.calls["px"] += 1
        return False

    async def refresh_session(guard=True):
        sm.calls["refresh_session"] += 1
        return False

    async def credential_relogin():
        sm.calls["credential_relogin"] += 1
        return False

    async def save_session_state():
        sm.calls["save"] += 1
        return True

    sm.refresh_access_token = refresh_access_token
    sm.get_page = get_page
    sm.get_access_token_status = get_access_token_status
    sm._trigger_token_refresh = trigger
    sm._px_challenge_parks_ladder = px
    sm.refresh_session = refresh_session
    sm._credential_relogin = credential_relogin
    sm.save_session_state = save_session_state
    sm._alert_critical = lambda msg: sm.alerts.append(msg)       # the real one appends to logs/error_log.txt
    return sm


# ── 1. ensure_fresh_access_token(force=True) ─────────────────────────────────
def test_ensure_fresh_access_token():
    with env(**ARMED):
        sm = make_sm()
        tab = Tab()
        r = run(sm.ensure_fresh_access_token(tab=tab, allow_nav=True, force=True))      # the executor's exact call
        check("eft_armed_force_returns_false", r is False, repr(r))
        check("eft_armed_never_calls_refresh_access_token", sm.calls["refresh_access_token"] == 0, str(sm.calls))
        check("eft_armed_touches_nothing", sm.calls["get_page"] == 0 and sm.calls["status"] == 0
              and not tab.gets and tab.evals == 0 and sm._last_token_repair_ts == 0.0,
              f"{sm.calls} gets={tab.gets} evals={tab.evals}")
        r = run(sm.ensure_fresh_access_token(force=True))                               # no tab handed in
        check("eft_armed_no_tab_does_not_even_fetch_a_page", r is False and sm.calls["get_page"] == 0, str(sm.calls))
        r = run(sm.ensure_fresh_access_token())                                         # the health-check form
        check("eft_armed_health_form_also_false_and_unchecked", r is False and sm.calls["status"] == 0
              and sm.calls["refresh_access_token"] == 0, str(sm.calls))
    for val in ("false", "no", "FALSE", "No"):                                          # the code's other off spellings
        with env(TARGET_TOKEN_KEEPFRESH=val):
            sm = make_sm()
            check(f"eft_off_spelling_{val}", run(sm.ensure_fresh_access_token(tab=Tab(), force=True)) is False
                  and sm.calls["refresh_access_token"] == 0)
    # MUTATION: KEEPFRESH=1 (the code default) must flip every assertion above.
    with env(TARGET_TOKEN_KEEPFRESH="1", TARGET_RELOGIN_MAX_PER_6H="0"):
        sm = make_sm()
        r = run(sm.ensure_fresh_access_token(tab=Tab(), allow_nav=True, force=True))
        check("eft_MUTATION_keepfresh1_force_reaches_refresh_access_token", r is True
              and sm.calls["refresh_access_token"] == 1, f"r={r} {sm.calls}")
    with env(TARGET_TOKEN_KEEPFRESH=None, TARGET_RELOGIN_MAX_PER_6H="0"):
        sm = make_sm()
        r = run(sm.ensure_fresh_access_token(tab=Tab(), force=True))
        check("eft_MUTATION_keepfresh_unset_is_on", r is True and sm.calls["refresh_access_token"] == 1, str(sm.calls))


# ── 2. _credential_relogin_allowed ───────────────────────────────────────────
def test_credential_relogin_allowed():
    with env(**ARMED):
        sm = make_sm()
        a = sm._credential_relogin_allowed()
        check("cra_armed_first_call_false", a is False, repr(a))
        check("cra_armed_records_no_attempt_time", sm._relogin_attempt_times == [], str(sm._relogin_attempt_times))
        check("cra_armed_alerts_capped_once", len(sm.alerts) == 1 and "CAPPED" in sm.alerts[0]
              and sm._relogin_capped_alerted is True, str(sm.alerts))
        b = sm._credential_relogin_allowed()
        check("cra_armed_stays_false_and_silent", b is False and sm._relogin_attempt_times == []
              and len(sm.alerts) == 1, f"{b} {sm._relogin_attempt_times} {sm.alerts}")
    # MUTATION: a non-zero budget lets the first call through and records it.
    with env(TARGET_TOKEN_KEEPFRESH="0", TARGET_RELOGIN_MAX_PER_6H="2"):
        sm = make_sm()
        a = sm._credential_relogin_allowed()
        check("cra_MUTATION_max2_first_call_true_and_recorded", a is True and len(sm._relogin_attempt_times) == 1
              and not sm.alerts, f"{a} {sm._relogin_attempt_times} {sm.alerts}")
    with env(TARGET_TOKEN_KEEPFRESH="0", TARGET_RELOGIN_MAX_PER_6H=None):
        sm = make_sm()
        check("cra_MUTATION_unset_is_the_default_2", sm._credential_relogin_allowed() is True)


# ── 3. ensure_logged_in never reaches the destructive relogin ────────────────
def test_ensure_logged_in_parks_the_heavy_ladder():
    with env(**ARMED, TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S="1800"), fast_sleep():
        sm = make_sm()
        r = run(sm.ensure_logged_in())
        check("eli_armed_returns_false", r is False, repr(r))
        check("eli_armed_never_calls_credential_relogin", sm.calls["credential_relogin"] == 0, str(sm.calls))
        check("eli_armed_never_mints_a_token", sm.calls["refresh_access_token"] == 0 and sm.calls["status"] == 0,
              str(sm.calls))
        check("eli_armed_walked_the_cheap_rungs_then_the_restart_rung",
              sm.calls["trigger"] == 2 and sm.calls["refresh_session"] == 1, str(sm.calls))
        check("eli_armed_relogin_gate_refused_and_nothing_recorded", sm._relogin_attempt_times == []
              and any("CAPPED" in m for m in sm.alerts), f"{sm._relogin_attempt_times} {sm.alerts}")
        check("eli_armed_parked_the_heavy_ladder", sm._dead_session_parked_until > time.time() + 1500,
              str(sm._dead_session_parked_until - time.time()))
        before = dict(sm.calls)
        r2 = run(sm.ensure_logged_in())                    # the next 5-min sentinel tick, still parked
        check("eli_armed_parked_tick_runs_no_nav_restart_or_relogin", r2 is False
              and sm.calls["trigger"] == before["trigger"] and sm.calls["refresh_session"] == before["refresh_session"]
              and sm.calls["credential_relogin"] == 0, f"{before} -> {sm.calls}")
    # MUTATION: a non-zero relogin budget with KEEPFRESH still 0 walks into the destructive rung.
    with env(TARGET_TOKEN_KEEPFRESH="0", TARGET_RELOGIN_MAX_PER_6H="2",
             TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S="1800"), fast_sleep():
        sm = make_sm()
        run(sm.ensure_logged_in())
        check("eli_MUTATION_max2_reaches_credential_relogin", sm.calls["credential_relogin"] == 1
              and len(sm._relogin_attempt_times) == 1, f"{sm.calls} {sm._relogin_attempt_times}")
    # MUTATION: KEEPFRESH=1 puts the token-mint rung (rung 0) back in front of the ladder.
    with env(TARGET_TOKEN_KEEPFRESH="1", TARGET_RELOGIN_MAX_PER_6H="0",
             TARGET_SENTINEL_DEAD_SESSION_BACKOFF_S="1800"), fast_sleep():
        sm = make_sm(refresh_result=False)
        async def _status(tab):
            sm.calls["status"] += 1
            return {"present": False, "member": False, "ttl_s": 0.0}
        sm.get_access_token_status = _status
        run(sm.ensure_logged_in())
        check("eli_MUTATION_keepfresh1_tries_to_mint_a_token", sm.calls["refresh_access_token"] >= 1
              and sm.calls["status"] >= 1, str(sm.calls))


# ── 4. the executor's mid-window dead-token repair (purchase_executor ~:3979) ─
def _exec_with_real_sm(**sm_kw):
    sm = make_sm(**sm_kw)
    ex = pe.PurchaseExecutor(session_manager=sm)
    tab = Tab(hb=401)
    tab.ex = ex
    ex._warmup_browser_ref = sm.browser
    ex._warmup_tabs = [tab]
    ex._warmup_tab_cart_ts = {0: time.time() - 5.2}        # warm tab: no /cart nav
    return ex, tab, sm


def test_executor_midwindow_repair():
    with env(**ARMED, TARGET_WRITE_AUTH_CONFIRM_N=None, TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=None):
        ex, tab, sm = _exec_with_real_sm()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), fast_sleep():
            run(ex._refresh_on_tab(0))
        text = out.getvalue()
        check("exec_armed_confirmed_dead_write_auth_branch_was_reached", "WRITE-AUTH DEAD" in text
              and "write-auth 401 CONFIRMED" in text, text[-500:])
        check("exec_armed_repair_attempt_reports_failed_no_mint", "background token repair: FAILED" in text
              and sm.calls["refresh_access_token"] == 0, f"{sm.calls} {text[-300:]}")
        check("exec_armed_repair_touches_no_page_or_status",
              sm.calls["get_page"] == 0 and sm.calls["status"] == 0 and sm._last_token_repair_ts == 0.0
              and sm.calls["credential_relogin"] == 0, str(sm.calls))
        check("exec_armed_no_nav_after_the_repair", tab.gets == [], str(tab.gets))
    # The repair-gate source is unchanged (the claim this pins hangs off it): the repair still exists and
    # still goes through the session manager's ensure_fresh_access_token(force=True), so KEEPFRESH=0 there
    # is what keeps it inert.
    src = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")
    check("exec_repair_call_is_the_force_true_ensure_fresh",
          "_sm.ensure_fresh_access_token(tab=tab, allow_nav=True, force=True)" in src)
    # MUTATION: KEEPFRESH=1 and the same confirmed 401 -> the repair mints a token and navs back to /cart.
    with env(TARGET_TOKEN_KEEPFRESH="1", TARGET_RELOGIN_MAX_PER_6H="0", TARGET_WRITE_AUTH_CONFIRM_N=None,
             TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR=None):
        ex, tab, sm = _exec_with_real_sm()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), fast_sleep():
            run(ex._refresh_on_tab(0))
        text = out.getvalue()
        check("exec_MUTATION_keepfresh1_repair_mints_a_token", sm.calls["refresh_access_token"] == 1
              and "OK — fresh member token" in text, f"{sm.calls} {text[-400:]}")
        check("exec_MUTATION_keepfresh1_repair_navs_to_cart", any("target.com/cart" in g for g in tab.gets), str(tab.gets))


def test_file_is_offline():
    """No Chrome and no network in this file: it never names a browser driver or a network library."""
    txt = Path(__file__).read_text(encoding="utf-8")
    banned = ("zend" + "river", "req" + "uests", "url" + "lib", "soc" + "ket", "nodri" + "ver", "curl_" + "cffi")
    hits = [b for b in banned if b in txt]
    check("file_names_no_browser_or_network_library", not hits, str(hits))


def main():
    for fn in (test_ensure_fresh_access_token, test_credential_relogin_allowed,
               test_ensure_logged_in_parks_the_heavy_ladder, test_executor_midwindow_repair,
               test_file_is_offline):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAIL:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Smoke test: 2026-09-10 proactive pre-wedge Chrome relaunch + fast wedge restart.

The run_20260910 soak proved the ~68-min CDP-dispatch wedge hits ONLY the two
PROXIED account Chromes (business/alt-1); the HOME-IP primary ran 18 h on one
launch, clean. These tests pin the mitigations added to SessionManager:

  1. ensure_logged_in proactively relaunches a PROXIED Chrome once it is older
     than TARGET_CHROME_MAX_AGE_S — BEFORE the wedge window.
  2. It NEVER relaunches the home-IP primary (proxy_url is None) — that Chrome
     is bound to the stock-monitor get_page loop and never wedges.
  3. A YOUNG proxied Chrome is left alone.
  4. purchase_in_progress skips the proactive relaunch (never restart under a shot).
  5. TARGET_CHROME_MAX_AGE_S=0 disables it entirely (kill-switch).
  6. _wedge_http_probe records _genuine_wedge_at only when the HTTP thread answers.
  7. The launch bat arms all three 2026-09-10 knobs.

No browser, no network. Run: python tests/test_chrome_age_relaunch_smoke.py
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

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  PASS {name}")
    except Exception as e:
        FAIL.append((name, e))
        print(f"  FAIL {name}: {type(e).__name__}: {e}")


def _mk_sm(tmp, *, proxy_url=None):
    sm = SessionManager(session_path=str(tmp / "t.json"),
                        user_data_dir=str(tmp / "prof"),
                        proxy_url=proxy_url,
                        account_id="acct")
    # Stub the two coroutines ensure_logged_in reaches so no browser is touched.
    sm._relaunch_calls = 0

    async def _fake_relaunch():
        sm._relaunch_calls += 1
        sm._browser_launched_at = time.time()   # a real relaunch resets the clock
        return True
    sm._relaunch_browser = _fake_relaunch

    async def _fake_token():
        return True
    sm.ensure_fresh_access_token = _fake_token
    # Keep the ladder from wandering past rung 0 in the no-relaunch cases.
    sm._dead_session_parked_until = 0.0
    return sm


import tempfile


def test_proxied_overage_chrome_relaunches():
    with tempfile.TemporaryDirectory() as d:
        sm = _mk_sm(Path(d), proxy_url="http://127.0.0.1:23002")
        sm._browser_launched_at = time.time() - 4000   # ~67 min old
        os.environ['TARGET_CHROME_MAX_AGE_S'] = '2100'
        try:
            ok = asyncio.run(sm.ensure_logged_in())
        finally:
            os.environ.pop('TARGET_CHROME_MAX_AGE_S', None)
        assert ok is True
        assert sm._relaunch_calls == 1, "over-age proxied Chrome was not proactively relaunched"


def test_home_ip_chrome_never_proactively_relaunches():
    with tempfile.TemporaryDirectory() as d:
        sm = _mk_sm(Path(d), proxy_url=None)          # home-IP primary
        sm._browser_launched_at = time.time() - 99999  # ancient
        os.environ['TARGET_CHROME_MAX_AGE_S'] = '2100'
        try:
            ok = asyncio.run(sm.ensure_logged_in())
        finally:
            os.environ.pop('TARGET_CHROME_MAX_AGE_S', None)
        assert ok is True
        assert sm._relaunch_calls == 0, "home-IP Chrome must never be age-relaunched"


def test_young_proxied_chrome_left_alone():
    with tempfile.TemporaryDirectory() as d:
        sm = _mk_sm(Path(d), proxy_url="http://127.0.0.1:23002")
        sm._browser_launched_at = time.time() - 300    # 5 min old
        os.environ['TARGET_CHROME_MAX_AGE_S'] = '2100'
        try:
            ok = asyncio.run(sm.ensure_logged_in())
        finally:
            os.environ.pop('TARGET_CHROME_MAX_AGE_S', None)
        assert ok is True
        assert sm._relaunch_calls == 0, "young Chrome must not be relaunched"


def test_purchase_in_progress_skips_relaunch():
    with tempfile.TemporaryDirectory() as d:
        sm = _mk_sm(Path(d), proxy_url="http://127.0.0.1:23002")
        sm._browser_launched_at = time.time() - 4000
        sm.purchase_in_progress = True                 # a shot is live
        os.environ['TARGET_CHROME_MAX_AGE_S'] = '2100'
        try:
            ok = asyncio.run(sm.ensure_logged_in())
        finally:
            os.environ.pop('TARGET_CHROME_MAX_AGE_S', None)
        assert ok is True
        assert sm._relaunch_calls == 0, "must never relaunch under an in-flight purchase"


def test_killswitch_disables_proactive_relaunch():
    with tempfile.TemporaryDirectory() as d:
        sm = _mk_sm(Path(d), proxy_url="http://127.0.0.1:23002")
        sm._browser_launched_at = time.time() - 99999
        os.environ['TARGET_CHROME_MAX_AGE_S'] = '0'
        try:
            ok = asyncio.run(sm.ensure_logged_in())
        finally:
            os.environ.pop('TARGET_CHROME_MAX_AGE_S', None)
        assert ok is True
        assert sm._relaunch_calls == 0, "TARGET_CHROME_MAX_AGE_S=0 must disable relaunch"


def test_wedge_probe_sets_genuine_flag_only_on_http_alive():
    """The genuine-wedge flag (fast-restart trigger) is set iff the HTTP thread
    answers while CDP is silent — never when the whole process is unresponsive."""
    with tempfile.TemporaryDirectory() as d:
        sm = _mk_sm(Path(d), proxy_url="http://127.0.0.1:23002")

        class _Cfg:
            host = '127.0.0.1'
            port = 65535   # nothing is listening → urlopen fails fast

        class _Browser:
            config = _Cfg()
        sm.browser = _Browser()
        sm._genuine_wedge_at = 0.0
        sm._wedge_probe_at = 0.0
        asyncio.run(sm._wedge_http_probe())
        # No server → the probe hit the "process unresponsive" branch → flag stays 0.
        assert sm._genuine_wedge_at == 0.0, "flag set even though HTTP thread did not answer"


def test_bat_arms_20260910_knobs():
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    for knob in ("TARGET_CHROME_MAX_AGE_S=2100",
                 "TARGET_WEDGE_FAST_RESTART=1",
                 "TARGET_REFILL_ON_WARM_MISS=1"):
        assert knob in bat, f"bat missing knob: {knob}"


if __name__ == "__main__":
    check("test_proxied_overage_chrome_relaunches", test_proxied_overage_chrome_relaunches)
    check("test_home_ip_chrome_never_proactively_relaunches", test_home_ip_chrome_never_proactively_relaunches)
    check("test_young_proxied_chrome_left_alone", test_young_proxied_chrome_left_alone)
    check("test_purchase_in_progress_skips_relaunch", test_purchase_in_progress_skips_relaunch)
    check("test_killswitch_disables_proactive_relaunch", test_killswitch_disables_proactive_relaunch)
    check("test_wedge_probe_sets_genuine_flag_only_on_http_alive", test_wedge_probe_sets_genuine_flag_only_on_http_alive)
    check("test_bat_arms_20260910_knobs", test_bat_arms_20260910_knobs)
    print()
    if FAIL:
        print(f"{len(PASS)}/{len(PASS) + len(FAIL)} passed — {len(FAIL)} FAILED")
        sys.exit(1)
    print(f"{len(PASS)}/{len(PASS)} smoke tests passed.")

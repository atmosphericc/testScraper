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


def _mk_sm(tmp, *, proxy_url=None, account_id="acct"):
    sm = SessionManager(session_path=str(tmp / "t.json"),
                        user_data_dir=str(tmp / "prof"),
                        proxy_url=proxy_url,
                        account_id=account_id)
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


# ── 2026-09-16 hot-sku plan P12 (INF-1): per-account offsets + sentinel skips ──
import contextlib  # noqa: E402
import io  # noqa: E402
import math  # noqa: E402

from src.session import session_manager as _smmod  # noqa: E402

_P12_ENV = ("TARGET_CHROME_MAX_AGE_S", "TARGET_CHROME_MAX_AGE_OFFSETS", "TARGET_SENTINEL_LOG_SKIPS")


def _clear_p12_env():
    for k in _P12_ENV:
        os.environ.pop(k, None)


def test_offsets_parse_and_clamp():
    p = _smmod._parse_chrome_age_offsets
    assert p("business:-300") == {"business": -300.0}
    assert p(" Business : -300 ; alt-1:120 ") == {"business": -300.0, "alt-1": 120.0}
    assert p("business:-300,alt-1:+60") == {"business": -300.0, "alt-1": 60.0}, "',' separator"
    assert p("business:-900") == {"business": -600.0}, "clamped to -600"
    assert p("alt-1:500") == {"alt-1": 240.0}, "clamped to +240"
    for bad in ("", None, "business", "business:", "business:abc", ":5", "business=-300",
                "business:nan", "business:inf", ";;", 12345):
        assert p(bad) == {}, f"malformed {bad!r} -> {p(bad)}"
    assert p("business:abc;alt-1:-60") == {"alt-1": -60.0}, "one bad entry skips only itself"


def test_effective_ages_business_1800_alt1_2100():
    eff = _smmod._effective_chrome_max_age
    raw = "business:-300"
    assert eff(2100.0, "business", raw) == 1800.0
    assert eff(2100.0, "alt-1", raw) == 2100.0
    assert eff(2100.0, "primary", raw) == 2100.0
    assert eff(2100.0, "BUSINESS", raw) == 1800.0, "account match is case-insensitive"
    # base + offset is clamped to [1500, 2340]
    assert eff(1900.0, "business", "business:-600") == 1500.0
    assert eff(2100.0, "business", "business:-600") == 1500.0
    assert eff(2300.0, "alt-1", "alt-1:240") == 2340.0
    assert eff(2100.0, "alt-1", "alt-1:240") == 2340.0
    assert eff(3000.0, "business", raw) == 2340.0
    # with one skipped 300 s sentinel tick, the latest relaunch age stays at
    # or under the earliest observed wedge (49 min = 2940 s)
    assert _smmod._CHROME_AGE_EFFECTIVE_MAX_S + 2 * 300 <= 2940
    assert 2100 + _smmod._CHROME_AGE_OFFSET_MAX_S <= _smmod._CHROME_AGE_EFFECTIVE_MAX_S


def test_effective_age_unchanged_when_empty_or_killed():
    eff = _smmod._effective_chrome_max_age
    for raw in ("", "   ", "garbage"):
        assert eff(2100.0, "business", raw) == 2100.0, raw
    assert eff(2100.0, None, "business:-300") == 2100.0, "no account id"
    assert eff(0.0, "business", "business:-300") == 0.0, "kill-switch stays a kill-switch"
    assert eff(-5.0, "business", "business:-300") == -5.0
    assert math.isnan(eff(float("nan"), "business", "business:-300"))
    assert eff(float("inf"), "business", "business:-300") == float("inf")
    _clear_p12_env()
    assert eff(2100.0, "business") == 2100.0, "env unset -> base"
    os.environ["TARGET_CHROME_MAX_AGE_OFFSETS"] = "business:-300"
    try:
        assert eff(2100.0, "business") == 1800.0, "env default read"
    finally:
        _clear_p12_env()


def _relaunch_run(account_id, age_s, env):
    _clear_p12_env()
    os.environ.update(env)
    buf = io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as d:
            sm = _mk_sm(Path(d), proxy_url="http://127.0.0.1:23002", account_id=account_id)
            sm._browser_launched_at = time.time() - age_s
            with contextlib.redirect_stdout(buf):
                ok = asyncio.run(sm.ensure_logged_in())
            assert ok is True
            return sm._relaunch_calls, buf.getvalue()
    finally:
        _clear_p12_env()


def test_offset_moves_business_relaunch_earlier():
    # 1900 s old: past business's 1800 s, under the 2100 s base.
    n, out = _relaunch_run("business", 1900, {"TARGET_CHROME_MAX_AGE_S": "2100",
                                              "TARGET_CHROME_MAX_AGE_OFFSETS": "business:-300"})
    assert n == 1, "business with a -300 offset must relaunch at 1900 s"
    assert "[max_age 1800s = base 2100s -300s via TARGET_CHROME_MAX_AGE_OFFSETS]" in out, out
    n, _ = _relaunch_run("alt-1", 1900, {"TARGET_CHROME_MAX_AGE_S": "2100",
                                         "TARGET_CHROME_MAX_AGE_OFFSETS": "business:-300"})
    assert n == 0, "alt-1 keeps the 2100 s base"
    n, _ = _relaunch_run("alt-1", 2200, {"TARGET_CHROME_MAX_AGE_S": "2100",
                                         "TARGET_CHROME_MAX_AGE_OFFSETS": "business:-300"})
    assert n == 1, "alt-1 still relaunches past 2100 s"


def test_offset_flag_empty_is_unchanged():
    for env in ({"TARGET_CHROME_MAX_AGE_S": "2100"},
                {"TARGET_CHROME_MAX_AGE_S": "2100", "TARGET_CHROME_MAX_AGE_OFFSETS": ""}):
        n, _ = _relaunch_run("business", 1900, env)
        assert n == 0, f"no offset -> no relaunch at 1900 s ({env})"
        n, out = _relaunch_run("business", 2200, env)
        assert n == 1
        lines = [ln for ln in out.splitlines() if ln.startswith("[CHROME-AGE]")]
        assert lines == ["[CHROME-AGE] business: proactive pre-wedge relaunch (37 min old)"], lines


def test_offset_never_revives_killswitch():
    n, _ = _relaunch_run("business", 99999, {"TARGET_CHROME_MAX_AGE_S": "0",
                                             "TARGET_CHROME_MAX_AGE_OFFSETS": "business:240"})
    assert n == 0, "TARGET_CHROME_MAX_AGE_S=0 must still disable the relaunch"


def _sentinel_run(flag, bad_label=False):
    import types
    from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager
    _clear_p12_env()
    if flag is not None:
        os.environ["TARGET_SENTINEL_LOG_SKIPS"] = flag
    m = object.__new__(BulletproofPurchaseManager)
    queued = []

    class _Fut:
        def add_done_callback(self, cb):
            pass

    def _run_async(coro):
        queued.append(coro)
        coro.close()
        return _Fut()

    def _label_busy():
        if bad_label:
            raise RuntimeError("label broke")
        return "business"

    busy = types.SimpleNamespace(session_manager=types.SimpleNamespace(purchase_in_progress=True),
                                 purchase_executor=None, label=_label_busy, run_async=_run_async)
    idle = types.SimpleNamespace(session_manager=types.SimpleNamespace(purchase_in_progress=False),
                                 purchase_executor=None, label=lambda: "alt-1", run_async=_run_async)
    m.worker_pool = types.SimpleNamespace(workers=[busy, idle])
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            m._run_session_sentinel_once()
    finally:
        _clear_p12_env()
    return len(queued), buf.getvalue()


def test_sentinel_skip_line():
    n, out = _sentinel_run("1")
    assert n == 1, "only the idle worker is checked"
    assert out.splitlines() == ["[SENTINEL] business: tick skipped (purchase in flight)"], out
    for flag in (None, "0", ""):
        n, out = _sentinel_run(flag)
        assert n == 1 and out == "", f"flag {flag!r} must stay silent: {out!r}"
    n, out = _sentinel_run(" 1 ")
    assert "tick skipped" in out, "flag value is stripped"
    n, out = _sentinel_run("1", bad_label=True)
    assert n == 1 and out == "", "a label error never breaks the tick"


if __name__ == "__main__":
    check("test_proxied_overage_chrome_relaunches", test_proxied_overage_chrome_relaunches)
    check("test_home_ip_chrome_never_proactively_relaunches", test_home_ip_chrome_never_proactively_relaunches)
    check("test_young_proxied_chrome_left_alone", test_young_proxied_chrome_left_alone)
    check("test_purchase_in_progress_skips_relaunch", test_purchase_in_progress_skips_relaunch)
    check("test_killswitch_disables_proactive_relaunch", test_killswitch_disables_proactive_relaunch)
    check("test_wedge_probe_sets_genuine_flag_only_on_http_alive", test_wedge_probe_sets_genuine_flag_only_on_http_alive)
    check("test_bat_arms_20260910_knobs", test_bat_arms_20260910_knobs)
    check("test_offsets_parse_and_clamp", test_offsets_parse_and_clamp)
    check("test_effective_ages_business_1800_alt1_2100", test_effective_ages_business_1800_alt1_2100)
    check("test_effective_age_unchanged_when_empty_or_killed", test_effective_age_unchanged_when_empty_or_killed)
    check("test_offset_moves_business_relaunch_earlier", test_offset_moves_business_relaunch_earlier)
    check("test_offset_flag_empty_is_unchanged", test_offset_flag_empty_is_unchanged)
    check("test_offset_never_revives_killswitch", test_offset_never_revives_killswitch)
    check("test_sentinel_skip_line", test_sentinel_skip_line)
    print()
    if FAIL:
        print(f"{len(PASS)}/{len(PASS) + len(FAIL)} passed — {len(FAIL)} FAILED")
        sys.exit(1)
    print(f"{len(PASS)}/{len(PASS)} smoke tests passed.")

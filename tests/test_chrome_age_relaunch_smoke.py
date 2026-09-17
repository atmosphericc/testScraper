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


# ── R1 review (R1-ARM-2, 2026-09-17): offsets alone do not de-lockstep ─────────
# Relaunches only happen on the fixed 300 s sentinel tick: business (1800 s)
# relaunches every 7 ticks, alt-1 (2100 s) every 8, so they still meet on the
# same tick every 56 ticks (~4.7 h). TARGET_CHROME_RELAUNCH_DESYNC_S defers the
# lower-ranked due relaunch by one tick. Simulated with the REAL
# ensure_logged_in on a fake clock over 12 h.
_DESYNC_ENV = "TARGET_CHROME_RELAUNCH_DESYNC_S"


import logging  # noqa: E402

_SILENT_LOG = logging.getLogger("chrome_age_desync_sim")
_SILENT_LOG.addHandler(logging.NullHandler())
_SILENT_LOG.propagate = False


class _SimTime:
    def __init__(self, t):
        self.t = float(t)

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


def _sim(desync, hours=12.0, boot=(0.0, 40.0), latency=8.0, first_tick=90.0,
         busy=(), order_flip=True, accounts=("business", "alt-1")):
    _clear_p12_env()
    os.environ.pop(_DESYNC_ENV, None)
    os.environ["TARGET_CHROME_MAX_AGE_S"] = "2100"
    os.environ["TARGET_CHROME_MAX_AGE_OFFSETS"] = "business:-300"
    if desync is not None:
        os.environ[_DESYNC_ENV] = desync
    T0 = 1_800_000_000.0
    clk = _SimTime(T0)
    saved_time = _smmod.time
    with _smmod._AGE_PEERS_LOCK:
        for p in list(_smmod._AGE_PEERS):
            _smmod._AGE_PEERS.discard(p)
    relaunches = []
    buf = io.StringIO()
    try:
        _smmod.time = clk
        with tempfile.TemporaryDirectory() as d:
            sms = {}
            for i, acct in enumerate(accounts):
                (Path(d) / acct).mkdir(parents=True, exist_ok=True)
                sm = _mk_sm(Path(d) / acct, proxy_url=f"http://127.0.0.1:2300{i}", account_id=acct)

                async def _relaunch(_sm=sm, _a=acct):
                    relaunches.append((_a, _sm._sim_tick, clk.t - _sm._browser_launched_at))
                    _sm._browser_launched_at = clk.t + latency
                    return True

                async def _ok(*a, **k):
                    return True

                sm._relaunch_browser = _relaunch
                sm.ensure_fresh_access_token = _ok
                sm.save_session_state = _ok
                sm.logger = _SILENT_LOG            # 288 ticks of [CHROME-AGE] warnings
                sm._browser_launched_at = T0 + boot[i]
                sms[acct] = sm
            k = 0
            while first_tick + 300.0 * k < hours * 3600.0:
                names = list(accounts)
                if order_flip and k % 2:
                    names.reverse()
                for j, acct in enumerate(names):
                    sm = sms[acct]
                    sm._sim_tick = k
                    clk.t = T0 + first_tick + 300.0 * k + 0.5 * j
                    sm.purchase_in_progress = (acct, k) in busy
                    with contextlib.redirect_stdout(buf):
                        asyncio.run(sm.ensure_logged_in())
                    sm.purchase_in_progress = False
                k += 1
    finally:
        _smmod.time = saved_time
        os.environ.pop(_DESYNC_ENV, None)
        _clear_p12_env()
    return relaunches, buf.getvalue()


def _same_tick(relaunches):
    by = {}
    for acct, tick, _ in relaunches:
        by.setdefault(tick, set()).add(acct)
    return sorted(t for t, s in by.items() if len(s) > 1)


def test_desync_offsets_alone_still_lockstep():
    rel, out = _sim(None)
    ticks = _same_tick(rel)
    assert ticks, "the finding's lockstep must reproduce with offsets alone"
    assert (90.0 + 300.0 * ticks[0]) / 3600.0 < 5.0, ticks
    assert "deferred one sentinel tick" not in out, out[-300:]


def test_desync_removes_same_tick_relaunches():
    for flip in (True, False):
        for boot in ((0.0, 40.0), (0.0, 0.0), (35.0, 5.0), (0.0, 250.0)):
            rel, out = _sim("120", boot=boot, order_flip=flip)
            assert not _same_tick(rel), (flip, boot, _same_tick(rel))
            ages = {}
            for acct, _, age in rel:
                ages.setdefault(acct, []).append(age)
            assert len(ages.get("business", [])) >= 15 and len(ages.get("alt-1", [])) >= 15, ages
            # alt-1 never yields; business yields at most one tick per cycle.
            assert max(ages["alt-1"]) <= 2100 + 300 + 1, max(ages["alt-1"])
            assert max(ages["business"]) <= 1800 + 600 + 1, max(ages["business"])
            if boot == (0.0, 40.0):
                assert "[CHROME-AGE] business: relaunch deferred one sentinel tick" in out, out[-400:]
                assert "alt-1: relaunch deferred" not in out, out[-400:]


def test_desync_deferral_plus_skipped_tick_stays_under_wedge_floor():
    # Worst case: the deferred business tick is followed by a tick skipped
    # under a purchase. Find the first deferral, then mark business busy on
    # the next tick.
    same = _same_tick(_sim(None)[0])
    k = same[0]
    rel, out = _sim("120", busy={("business", k + 1)})
    assert not _same_tick(rel), _same_tick(rel)
    worst = max(age for acct, _, age in rel)
    assert worst < 2940.0, worst


def test_desync_parse_and_pure_rule():
    f = _smmod._chrome_relaunch_desync_s
    got = {v: f(v) for v in ("0", "", " ", "x", "-5", "nan", "inf", "10", "120", " 120 ", "299", "280")}
    assert got == {"0": 0.0, "": 0.0, " ": 0.0, "x": 0.0, "-5": 0.0, "nan": 0.0, "inf": 0.0,
                   "10": 30.0, "120": 120.0, " 120 ": 120.0, "299": 280.0, "280": 280.0}, got
    os.environ.pop(_DESYNC_ENV, None)
    assert f() == 0.0, "unset = off"
    r = _smmod._desync_defer_reason
    me_b = {"id": "business", "max_age": 1800.0}
    me_a = {"id": "alt-1", "max_age": 2100.0}
    due_a = {"id": "alt-1", "max_age": 2100.0, "launched_at": 1000.0, "relaunch_at": 0.0, "busy": False}
    due_b = {"id": "business", "max_age": 1800.0, "launched_at": 1000.0, "relaunch_at": 0.0, "busy": False}
    now = 1000.0 + 2500.0
    assert "alt-1 is due" in r(me_b, [due_a], now, 120.0)
    assert r(me_a, [due_b], now, 120.0) == "", "the larger max age keeps its tick"
    assert r(me_b, [dict(due_a, busy=True)], now, 120.0) == "", "a busy peer will not relaunch"
    assert r(me_b, [dict(due_a, launched_at=now - 100)], now, 120.0) == "", "a young peer is not due"
    assert r(me_b, [dict(due_a, relaunch_at=now - 60)], now, 120.0).startswith("alt-1 relaunched 60s ago")
    assert r(me_a, [dict(due_b, relaunch_at=now - 60)], now, 120.0).startswith("business relaunched"), \
        "a relaunch that already happened is spaced regardless of rank"
    assert r(me_b, [dict(due_a, relaunch_at=now - 200)], now, 120.0) == ""
    assert r(me_b, [], now, 120.0) == ""
    tie_x = {"id": "x", "max_age": 2100.0}
    tie_y = dict(due_a, id="y")
    assert (r(tie_x, [tie_y], now, 120.0) == "") != (r({"id": "y", "max_age": 2100.0},
                                                       [dict(tie_y, id="x")], now, 120.0) == ""), \
        "an equal max age is broken deterministically (exactly one side yields)"


def test_desync_bat_pin():
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("utf-8", "replace")
    lines = bat.split("\r\n")
    assert lines.count("set TARGET_CHROME_RELAUNCH_DESYNC_S=120") == 1, \
        [ln for ln in lines if "DESYNC" in ln]
    val = None
    for ln in lines:
        if ln.strip().lower().startswith("set target_chrome_relaunch_desync_s="):
            val = ln.strip().split("=", 1)[1]
    assert val == "120" and _smmod._chrome_relaunch_desync_s(val) == 120.0, val


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
    check("test_desync_offsets_alone_still_lockstep", test_desync_offsets_alone_still_lockstep)
    check("test_desync_removes_same_tick_relaunches", test_desync_removes_same_tick_relaunches)
    check("test_desync_deferral_plus_skipped_tick_stays_under_wedge_floor",
          test_desync_deferral_plus_skipped_tick_stays_under_wedge_floor)
    check("test_desync_parse_and_pure_rule", test_desync_parse_and_pure_rule)
    check("test_desync_bat_pin", test_desync_bat_pin)
    print()
    if FAIL:
        print(f"{len(PASS)}/{len(PASS) + len(FAIL)} passed — {len(FAIL)} FAILED")
        sys.exit(1)
    print(f"{len(PASS)}/{len(PASS)} smoke tests passed.")

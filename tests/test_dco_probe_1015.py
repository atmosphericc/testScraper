#!/usr/bin/env python3
"""Offline tests: DCO-PROBE-1015 (2026-10-15) -- sub-second DCO burst re-POST as a randomized
in-night A/B arm.

  TARGET_DCO_PROBE_P=<0..1>        unset / 0 / junk / NaN / inf = OFF (byte-identical to HEAD)
  TARGET_DCO_PROBE_MIN_S / _MAX_S  probe sleep after the FS response (default 0.15 / 0.40, floor 0.05)

For each burst-eligible ATC-level FAST_SELLING event that would be re-POST #1, the race thread
draws the arm BEFORE the bank wait: probe (prob P) or std (the 09-17 burst, untouched). The probe
arm skips the bank wait (banked set if ready at t=0, else page-signed), sleeps the probe band
instead of the 1.0 s clamp / cart-hold-interval floor, and FORCES the executor's cart-hold read
after its answer (review 2026-09-20 F6 kept). Everything else -- re-POST #2, the _dco_body test,
the room check, the cap, every edge / 401 path -- is unchanged.

These tests drive the REAL race loop (_start_real_purchase -> racer threads -> the retry loop)
against stub workers on a recorded `time.sleep`; the cart-hold checks run the REAL
PurchaseExecutor._check_cart_hold against a stub tab. No browser, no network, no bot.
Run: python tests/test_dco_probe_1015.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import math
import os
import random as _random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402  (env, quiet, check, race_mgr, _Worker, _Exec, _race)
import test_mass_restock_1006 as MR  # noqa: E402  (FLAGS)
import test_fast_cadence_1011 as FC  # noqa: E402  (_FakeTime, _fake_sleep, _bat_env, _VirtTime)
import src.purchasing.bulletproof_purchase_manager as bpm  # noqa: E402
import src.session.purchase_executor as pe  # noqa: E402

check, env, quiet = H.check, H.env, H.quiet
HOT = H.HOT_TCIN
NEW = ("TARGET_DCO_PROBE_P", "TARGET_DCO_PROBE_MIN_S", "TARGET_DCO_PROBE_MAX_S")
for _k in NEW:
    os.environ.pop(_k, None)

FS_BODY = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
DCO = {"success": False, "reason": "rate_limited_429", "gate_kind": "dco", "gate_body": FS_BODY,
       "error": "ATC rate-limited (429)"}
DCO_EDGE_BODY = dict(DCO, gate_body='{"message":"Rate Limited","code":"ERR_A2C_TCIN_RATE_LIMITED"}')
EDGE = {"success": False, "reason": "rate_limited_429", "gate_kind": "edge", "error": "ATC rate-limited (429)"}
A401 = {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401", "error": "ATC 401"}
WIN = {"success": True, "order_number": "PROBE-1", "quantity": 1}

# The 09-17 burst as the wrapper arms it (MIN/MAX 1.0/1.5, cap 2, REQUIRE_BANK=0, BANK_WAIT 2 s),
# wave-first on, a 60 s budget so a 55 s cold re-entry never fits (the window ends after the burst),
# sleeps on a recorder.
BASE_ENV = dict(
    TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0", TARGET_RETRY_WARM="0",
    TARGET_WAVE_FIRST_ONLY="1", TARGET_WAVE_FIRST_EDGE="1", TARGET_SHOT_BANK_GATE="0",
    TARGET_401_PULSE="0", TARGET_RACE_ALL_WORKERS="1", TARGET_RETRY_WHILE_IN_STOCK="1",
    TARGET_RETRY_WHILE_IN_STOCK_MAX="20", TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="60",
    TARGET_ATC_DCO_AS_EDGE_CADENCE="0", TARGET_IDENTITY_REST="0", TARGET_PARK_ACCOUNT_TCINS=None,
    TARGET_RETRY_STOP_WHEN_OOS="0", TARGET_MULTI_SKU_DISPATCH=None, TARGET_FAST_CADENCE_IDENTS=None,
    TARGET_ATC_DCO_BURST="1", TARGET_ATC_DCO_BURST_MIN_S="1.0", TARGET_ATC_DCO_BURST_MAX_S="1.5",
    TARGET_ATC_DCO_BURST_MAX="2", TARGET_ATC_DCO_BURST_REQUIRE_BANK="0",
    TARGET_ATC_DCO_BURST_BANK_WAIT_S="2", TARGET_ATC_DCO_BURST_STUCK_NOWAIT="0",
    TARGET_ATC_DCO_BURST_ANY_BODY="0", TARGET_CART_HOLD_CHECK_INTERVAL_S="1.0",
    TARGET_DCO_PROBE_P=None, TARGET_DCO_PROBE_MIN_S=None, TARGET_DCO_PROBE_MAX_S=None,
)
PROBE_LO, PROBE_HI = 0.15, 0.40
STD_LO, STD_HI = 1.0, 1.5


def _in(x, lo, hi):
    return lo - 1e-9 <= x <= hi + 1e-9


def _probe_lines(out):
    return [l for l in out.splitlines() if l.startswith("[DCO_PROBE]")]


# --------------------------------------------------------------------------- stubs

class _Bank:
    def __init__(self, age):
        self._a = age

    def newest_age(self):
        return self._a


class _BankExec(H._Exec):
    """Stub executor with the harvest API the burst reads. `ready` repeats its last value; a wait
    flips readiness when `ready_after_wait`. `events` is the ordered readiness / wait trail."""
    def __init__(self, script, ready=(False,), ready_after_wait=False, bank_age=None):
        super().__init__(script)
        self._harvest_cfg = {"enabled": True}
        self._harvest_replay_on = True
        self.ready = list(ready)
        self.ready_after_wait = ready_after_wait
        self.events = []
        self.waits = []
        self._shape_bank = _Bank(bank_age)

    def harvest_set_ready(self, margin_s=0.0):
        self.events.append("ready")
        return self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]

    async def harvest_wait_for_set(self, max_wait_s, margin_s=0.0):
        self.events.append("wait")
        self.waits.append(float(max_wait_s))
        if self.ready_after_wait:
            self.ready = [True]
        return bool(self.ready[0])


class _Tab:
    """Stub tab for the REAL _check_cart_hold: each evaluate() pops one scripted cart read."""
    def __init__(self, reads):
        self.reads = list(reads)
        self.n = 0

    async def evaluate(self, js, await_promise=False):
        self.n += 1
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]


class _HoldExec(H._Exec):
    """Stub executor whose denied (DCO / 401) shots run the REAL PurchaseExecutor._check_cart_hold
    (interval skip and all) against a stub tab; a silent hold turns the shot into a success, as
    the real execute_purchase does (cart_confirmed -> checkout)."""
    def __init__(self, script, reads, interval=1.0):
        super().__init__(script)
        self._cart_hold_check_on = True
        self._cart_hold_check_interval_s = interval
        self._last_cart_hold_check_ts = 0.0
        self._cart_hold_verbose = False
        self.tab = _Tab(reads)
        self.ts_at_entry = []
        self.hold_results = []

    def _ident_tag(self):
        return "stub"

    async def execute_purchase(self, tcin, quantity=1):
        self.ts_at_entry.append(self._last_cart_hold_check_ts)
        self.calls += 1
        r = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if not r.get("success") and r.get("gate_kind") in ("dco", "auth401"):
            held = await pe.PurchaseExecutor._check_cart_hold(self, self.tab, tcin, 429)
            self.hold_results.append(held)
            if held:
                return {"success": True, "order_number": "HELD-1", "quantity": 1, "tcin": tcin}
        return dict(r, tcin=tcin)


class _RandSpy:
    """Stands in for the manager module's `random`: `random()` returns the scripted draws (last
    repeats) and counts calls; everything else is the real module."""
    def __init__(self, vals):
        self.vals = list(vals)
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.vals.pop(0) if len(self.vals) > 1 else self.vals[0]

    def __getattr__(self, name):
        return getattr(_random, name)


@contextlib.contextmanager
def _rand(spy):
    saved = bpm.random
    bpm.random = spy
    try:
        yield spy
    finally:
        bpm.random = saved


@contextlib.contextmanager
def _patched(name, fn):
    saved = getattr(bpm, name)
    setattr(bpm, name, fn)
    try:
        yield
    finally:
        setattr(bpm, name, saved)


def _race1(script, extra=None, executor=None, mod=None, seed=None):
    """One real single-worker race (label `auto`) on a recorded sleep. Returns
    (ok, out, sleeps, manager, executor)."""
    mod = mod or bpm
    ws = [H._Worker(1, "primary", list(script))]
    if executor is not None:
        ws[0].purchase_executor = executor
    m = H.race_mgr(ws)
    if mod is not bpm:
        m.__class__ = mod.BulletproofPurchaseManager
    ft = FC._FakeTime()
    saved = mod.time
    mod.time = ft
    if seed is not None:
        _random.seed(seed)
    try:
        with env(**{**MR.FLAGS, **BASE_ENV, **(extra or {})}):
            ok, out = quiet(H._race, m, HOT)
    finally:
        mod.time = saved
    return ok, out, list(ft.rec), m, ws[0].purchase_executor


# --------------------------------------------------------------------------- 1. config + helpers

def test_cfg_parsing():
    c = bpm._dco_probe_cfg({})
    check("cfg_unset_off", c == {"p": 0.0, "lo": PROBE_LO, "hi": PROBE_HI}, c)
    for raw in ("0", "0.0", "", " ", "junk", "nan", "NaN", "inf", "-inf", "Infinity", "-1", "-0.5", "1e-999",
                "None", "0x1"):
        check(f"cfg_off_for_{raw!r}", bpm._dco_probe_cfg({"TARGET_DCO_PROBE_P": raw})["p"] == 0.0, raw)
    check("cfg_none_value_off", bpm._dco_probe_cfg({"TARGET_DCO_PROBE_P": None})["p"] == 0.0)
    check("cfg_half", bpm._dco_probe_cfg({"TARGET_DCO_PROBE_P": " 0.5 "})["p"] == 0.5)
    check("cfg_one", bpm._dco_probe_cfg({"TARGET_DCO_PROBE_P": "1"})["p"] == 1.0)
    check("cfg_over_one_clamps", bpm._dco_probe_cfg({"TARGET_DCO_PROBE_P": "7"})["p"] == 1.0)
    c = bpm._dco_probe_cfg({"TARGET_DCO_PROBE_P": "1", "TARGET_DCO_PROBE_MIN_S": "0.2", "TARGET_DCO_PROBE_MAX_S": "0.3"})
    check("cfg_band", (c["lo"], c["hi"]) == (0.2, 0.3), c)
    c = bpm._dco_probe_cfg({"TARGET_DCO_PROBE_MIN_S": "0.01", "TARGET_DCO_PROBE_MAX_S": "0.02"})
    check("cfg_floor_005", (c["lo"], c["hi"]) == (0.05, 0.05), c)
    c = bpm._dco_probe_cfg({"TARGET_DCO_PROBE_MIN_S": "3", "TARGET_DCO_PROBE_MAX_S": "9"})
    check("cfg_cap_1s_sub_second_only", (c["lo"], c["hi"]) == (1.0, 1.0), c)
    c = bpm._dco_probe_cfg({"TARGET_DCO_PROBE_MIN_S": "0.5", "TARGET_DCO_PROBE_MAX_S": "0.2"})
    check("cfg_hi_raised_to_lo", (c["lo"], c["hi"]) == (0.5, 0.5), c)
    c = bpm._dco_probe_cfg({"TARGET_DCO_PROBE_MIN_S": "x", "TARGET_DCO_PROBE_MAX_S": "nan"})
    check("cfg_garbage_band_defaults", (c["lo"], c["hi"]) == (PROBE_LO, PROBE_HI), c)
    c = bpm._dco_probe_cfg({"TARGET_DCO_PROBE_MIN_S": "inf", "TARGET_DCO_PROBE_MAX_S": "-inf"})
    check("cfg_inf_band_defaults", (c["lo"], c["hi"]) == (PROBE_LO, PROBE_HI), c)
    # the std burst cfg is untouched: no probe key leaked into it (test_dco_burst pins the full dict)
    check("cfg_burst_cfg_has_no_probe_keys", not any("probe" in k for k in bpm._dco_burst_cfg({})))
    with env(TARGET_DCO_PROBE_P="0.25"):
        check("cfg_reads_os_environ_by_default", bpm._dco_probe_cfg()["p"] == 0.25)
    check("cfg_os_environ_unset_default_off", bpm._dco_probe_cfg()["p"] == 0.0)


def test_helpers():
    st = _random.getstate()
    check("draw_p0_false_and_no_rng_use", bpm._dco_probe_draw({"p": 0.0}) is False and _random.getstate() == st)
    check("draw_missing_p_false", bpm._dco_probe_draw({}) is False and _random.getstate() == st)
    check("draw_bad_cfg_false", bpm._dco_probe_draw(None) is False and bpm._dco_probe_draw({"p": "x"}) is False)
    check("draw_p1_always", all(bpm._dco_probe_draw({"p": 1.0}) for _ in range(200)))
    with _rand(_RandSpy([0.49])):
        check("draw_half_below_is_probe", bpm._dco_probe_draw({"p": 0.5}) is True)
    with _rand(_RandSpy([0.5])):
        check("draw_half_at_is_std", bpm._dco_probe_draw({"p": 0.5}) is False)
    with _rand(_RandSpy([0.99])):
        check("draw_half_above_is_std", bpm._dco_probe_draw({"p": 0.5}) is False)
    n = sum(bpm._dco_probe_draw({"p": 0.3}) for _ in range(4000))
    check("draw_rate_about_p", 1000 < n < 1400, n)

    class E:
        _last_cart_hold_check_ts = 123.0

    class NoAttr:
        pass

    class Boom:
        def __setattr__(self, k, v):
            raise RuntimeError("x")
        _last_cart_hold_check_ts = 5.0

    e = E()
    check("force_hold_resets_stamp", bpm._dco_probe_force_hold(e) is True and e._last_cart_hold_check_ts == 0.0)
    check("force_hold_no_attr_false", bpm._dco_probe_force_hold(NoAttr()) is False)
    check("force_hold_swallows_errors", bpm._dco_probe_force_hold(Boom()) is False)
    check("force_hold_none_executor", bpm._dco_probe_force_hold(None) is False)

    class WB:
        _shape_bank = _Bank(31.4)

    class WN:
        _shape_bank = _Bank(None)

    class WX:
        class _shape_bank:
            @staticmethod
            def newest_age():
                raise RuntimeError("x")
    check("bank_age_value", bpm._dco_probe_bank_age(WB()) == 31.4)
    check("bank_age_none_when_empty", bpm._dco_probe_bank_age(WN()) is None)
    check("bank_age_none_without_bank", bpm._dco_probe_bank_age(object()) is None)
    check("bank_age_swallows_errors", bpm._dco_probe_bank_age(WX()) is None)

    a = bpm._dco_probe_line("probe", 0.274, "W1/primary", "123", 0.0, True, 31.4)
    check("line_probe_format", a == "[DCO_PROBE] arm=probe delay=0.27 ident=W1/primary tcin=123 "
                                    "waited=0.0 cred=banked bank_age=31", a)
    b = bpm._dco_probe_line("std", 1.26, "W3/alt-1", "9", 1.97, False, None)
    check("line_std_format", b == "[DCO_PROBE] arm=std delay=1.26 ident=W3/alt-1 tcin=9 "
                                  "waited=2.0 cred=page bank_age=na", b)
    c = bpm._dco_probe_line("probe", 0.0, "auto", "9", 0.0, False, None, nofire="no_bank")
    check("line_nofire_format", c == "[DCO_PROBE] arm=probe nofire=no_bank ident=auto tcin=9 "
                                     "waited=0.0 cred=page bank_age=na", c)
    check("line_never_raises", bpm._dco_probe_line("probe", "x", "i", "t").startswith("[DCO_PROBE] arm=probe"))
    # the spec'd prefix stays greppable
    check("line_spec_regex", re.match(r"\[DCO_PROBE\] arm=(probe|std) delay=[\d.]+ ident=\S+ tcin=\d+", a)
          is not None)


# --------------------------------------------------------------------------- 2. flag off == HEAD

BASE_COMMIT = "4e624390ac064762bffb1afdc482f14e0118d7b9"     # the commit this item was built on
_BASE = {}


def _load_base():
    """The manager module as of BASE_COMMIT (git show), or None when git / the commit is not
    reachable. Loaded under its own name so it never shadows the real module."""
    if "mod" in _BASE:
        return _BASE["mod"]
    mod = None
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "show",
                            f"{BASE_COMMIT}:src/purchasing/bulletproof_purchase_manager.py"],
                           capture_output=True, timeout=60)
        if r.returncode == 0 and r.stdout:
            p = Path(tempfile.mkdtemp(prefix="bpm_base_")) / "bpm_base_1015.py"
            p.write_bytes(r.stdout)
            spec = importlib.util.spec_from_file_location("src.purchasing._bpm_base_1015", str(p))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        print(f"[NOTE] base module not loadable: {type(e).__name__}: {e}")
        mod = None
    _BASE["mod"] = mod
    return mod


def _norm(out):
    return re.sub(r"t-left=\d+s|\d+s of window left", "N", out)


def _snap(script, extra, mod, seed, executor=None):
    ok, out, sleeps, m, ex = _race1(script, extra, executor=executor, mod=mod, seed=seed)
    return {"ok": ok, "sleeps": sleeps, "calls": ex.calls, "out": _norm(out),
            "rng": _random.getstate(), "events": list(getattr(ex, "events", []))}


OFF_VALUES = (None, "0", "0.0", "", " ", "junk", "nan", "inf", "-inf", "-1", "1e-999")


def _same(a, b, tol=0.0):
    """Snapshots equal; sleeps within `tol` (the bank-wait scenarios fold the REAL elapsed wait
    into the sleep, so they jitter by milliseconds between two identical runs)."""
    if a.keys() != b.keys() or any(a[k] != b[k] for k in a if k != "sleeps"):
        return False
    if len(a["sleeps"]) != len(b["sleeps"]):
        return False
    return all(abs(x - y) <= tol for x, y in zip(a["sleeps"], b["sleeps"]))
SCENARIOS = (
    ("burst_win", [DCO, DCO, WIN], {}),
    ("burst_cap_then_wave_first", [DCO, DCO, DCO, DCO], {}),
    ("burst_then_edge", [DCO, EDGE, WIN], {}),
    ("burst_then_401", [DCO, A401, WIN], {}),
    ("edge_first", [EDGE, EDGE, EDGE], {}),
    ("edge_key_body_no_burst", [DCO_EDGE_BODY, WIN], {}),
    ("no_room", [DCO, DCO, WIN], {"TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "15"}),
    ("burst_off", [DCO, DCO, WIN], {"TARGET_ATC_DCO_BURST": "0"}),
)


def test_flag_off_is_byte_identical():
    """P unset / 0 / junk / NaN / inf, through the real race loop, on a seeded RNG: the sleep
    sequence, the executor calls, the whole stdout and the RNG state (no extra draw) equal the
    unset run -- and equal the manager as of BASE_COMMIT when git can show it."""
    base = _load_base()
    if base is None:
        print(f"[SKIP] differential vs {BASE_COMMIT[:8]}: git / commit not reachable (self-consistency only)")
    for si, (name, script, extra) in enumerate(SCENARIOS):
        ref = _snap(script, extra, bpm, 11)
        check(f"off_{name}_ran", ref["ok"] and ref["calls"] >= 1, (ref["ok"], ref["calls"]))
        check(f"off_{name}_no_probe_line", "[DCO_PROBE]" not in ref["out"], ref["out"][-400:])
        # every off spelling on the first scenario; the rest take the three that matter (suite file
        # timeout is 100 s)
        for v in (OFF_VALUES if si == 0 else (None, "0", "nan")):
            got = _snap(script, {**extra, "TARGET_DCO_PROBE_P": v}, bpm, 11)
            same = got == ref
            check(f"off_{name}_P={v!r}_identical", same,
                  [k for k in ref if ref[k] != got[k]])
        if base is not None:
            b = _snap(script, extra, base, 11)
            check(f"off_{name}_matches_base_sleeps_calls", (ref["sleeps"], ref["calls"], ref["ok"]) ==
                  (b["sleeps"], b["calls"], b["ok"]), (ref["sleeps"], b["sleeps"]))
            check(f"off_{name}_matches_base_stdout", ref["out"] == b["out"],
                  [l for l in ref["out"].splitlines() if l not in b["out"].splitlines()][:3])
            check(f"off_{name}_matches_base_rng_state", ref["rng"] == b["rng"])
    # the bank paths too (REQUIRE_BANK=1 with and without a set; wait that delivers one)
    for tag, mk, extra in (
        ("bank_never", lambda: _BankExec([DCO, WIN], ready=(False,)), {"TARGET_ATC_DCO_BURST_REQUIRE_BANK": "1"}),
        ("bank_after_wait", lambda: _BankExec([DCO, DCO, WIN], ready=(False,), ready_after_wait=True),
         {"TARGET_ATC_DCO_BURST_REQUIRE_BANK": "1"}),
        ("bank_ready", lambda: _BankExec([DCO, DCO, WIN], ready=(True,), bank_age=20.0), {}),
    ):
        ref = _snap([DCO], extra, bpm, 5, executor=mk())
        for v in (None, "0", "inf"):
            got = _snap([DCO], {**extra, "TARGET_DCO_PROBE_P": v}, bpm, 5, executor=mk())
            check(f"off_{tag}_P={v!r}_identical", _same(got, ref, 0.05), [k for k in ref if ref[k] != got[k]])
        check(f"off_{tag}_no_probe_line", "[DCO_PROBE]" not in ref["out"])
        if base is not None:
            b = _snap([DCO], extra, base, 5, executor=mk())
            check(f"off_{tag}_matches_base", _same(ref, b, 0.05), [k for k in ref if ref[k] != b[k]])


# --------------------------------------------------------------------------- 3. probe arm

def test_probe_arm_p1():
    """P=1: re-POST #1 is the probe -- chosen before the bank wait, no bank wait, a sleep inside
    the probe band; re-POST #2 is the unchanged std burst (its bank wait and 1.0-1.5 s sleep)."""
    ex = _BankExec([DCO, DCO, WIN], ready=(False,))
    ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1"}, executor=ex, seed=3)
    pl = _probe_lines(out)
    check("p1_race_finished_and_won", ok and ex.calls == 3 and
          m._states.get(HOT, {}).get("status") == "purchased", (ok, ex.calls, m._states.get(HOT)))
    check("p1_one_probe_line", len(pl) == 1 and "arm=probe" in pl[0], pl)
    check("p1_line_fields", re.match(r"\[DCO_PROBE\] arm=probe delay=\d\.\d\d ident=auto tcin=%s waited=0\.0 "
                                     r"cred=page bank_age=na$" % HOT, pl[0] if pl else "") is not None, pl)
    check("p1_two_sleeps", len(sl) == 2, sl)
    check("p1_probe_sleep_in_band", sl and _in(sl[0], PROBE_LO, PROBE_HI), sl)
    check("p1_logged_delay_is_the_sleep", pl and abs(float(re.search(r"delay=([\d.]+)", pl[0]).group(1)) - sl[0]) < 0.006,
          (pl, sl))
    check("p1_repost2_is_std_band", len(sl) == 2 and _in(sl[1], STD_LO, STD_HI), sl)
    # no bank wait for the probe; the std re-POST #2 waits the configured 2 s once
    check("p1_probe_no_bank_wait_std_waits_once", ex.waits == [2.0] and ex.events[0] == "ready"
          and ex.events[1:] == ["ready", "wait", "ready"], (ex.waits, ex.events))
    check("p1_burst_line_shows_probe_band", "re-POST 1/2 in 0.15-0.40s instead of the cold re-entry" in out
          and "re-POST 2/2 in 1.0-1.5s instead of the cold re-entry" in out, out[-900:])

    # a banked set ready at t=0 rides the probe: cred=banked, bank_age logged, still no wait
    ex = _BankExec([DCO, DCO, WIN], ready=(True,), bank_age=42.0)
    ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1"}, executor=ex, seed=3)
    pl = _probe_lines(out)
    check("p1_banked_cred_and_age", len(pl) == 1 and "cred=banked bank_age=42" in pl[0] and "waited=0.0" in pl[0], pl)
    check("p1_banked_no_wait", ex.waits == [] and "[DCO_BURST]" in out, ex.waits)
    check("p1_banked_burst_line_bank_true", "bank=True waited=0.0s" in out, out[-600:])

    # the band knobs move the sleep; the 0.05 floor and the 1.0 cap hold
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_DCO_PROBE_MIN_S": "0.2",
                                                  "TARGET_DCO_PROBE_MAX_S": "0.2"}, seed=3)
    check("p1_custom_band_exact", ok and sl and abs(sl[0] - 0.2) < 1e-9, sl)
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_DCO_PROBE_MIN_S": "0.001",
                                                  "TARGET_DCO_PROBE_MAX_S": "0.001"}, seed=3)
    check("p1_floor_005", ok and sl and abs(sl[0] - 0.05) < 1e-9, sl)
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_DCO_PROBE_MIN_S": "5",
                                                  "TARGET_DCO_PROBE_MAX_S": "9"}, seed=3)
    check("p1_cap_1s", ok and sl and abs(sl[0] - 1.0) < 1e-9, sl)

    # the std BURST knobs do not leak into the probe: a 5 s std band, probe still sub-second
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_BURST_MIN_S": "4",
                                                  "TARGET_ATC_DCO_BURST_MAX_S": "5",
                                                  "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "80"}, seed=3)
    check("p1_std_knobs_dont_touch_probe", ok and len(sl) == 2 and _in(sl[0], PROBE_LO, PROBE_HI) and _in(sl[1], 4.0, 5.0), sl)

    # the arm is drawn BEFORE the bank wait: pinned in the source order too
    src = Path(bpm.__file__).read_text(encoding="utf-8")
    i = src.find("_dco_probe_label = 'probe' if _dco_probe_draw(_dco_probe) else 'std'")
    j = src.find("_bank_ready, _dco_burst_waited = _dco_burst_bank_ready(")
    check("p1_src_draw_before_bank_ready", 0 < i < j, (i, j))


def test_probe_with_dco_as_edge_and_fast_cadence_and_yield():
    """The probe wins over the edge-cadence block (DCO-as-edge armed), over FX-1011 fast cadence
    and rides the yield-aware sleep without error (the single sleep sites are reused)."""
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_AS_EDGE_CADENCE": "1",
                                                  "TARGET_ATC_EDGE429_RETRY_DELAY_MIN": "3.0",
                                                  "TARGET_ATC_EDGE429_RETRY_DELAY_MAX": "3.0"}, seed=3)
    check("edge_cadence_probe_wins", ok and len(sl) == 2 and _in(sl[0], PROBE_LO, PROBE_HI) and _in(sl[1], STD_LO, STD_HI), sl)
    check("edge_cadence_no_retry_cadence_line", "[RETRY_CADENCE]" not in out, out[-500:])
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_FAST_CADENCE_IDENTS": "all"}, seed=3)
    check("fast_cadence_probe_wins", ok and len(sl) == 2 and _in(sl[0], PROBE_LO, PROBE_HI) and _in(sl[1], STD_LO, STD_HI), sl)
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_FAST_CADENCE_IDENTS": "all",
                                                  "TARGET_MULTI_SKU_MISS_REARM": "1",
                                                  "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}, seed=3)
    pl = _probe_lines(out)
    check("yield_aware_sleep_probe_ok", ok and len(pl) == 1 and sl and _in(sl[0], PROBE_LO, PROBE_HI), (sl, pl))


# --------------------------------------------------------------------------- 4. std arm

def test_std_arm_unchanged():
    """P=0.5 with the draw forced to 0.99: the std arm. Same 1.0-1.5 s sleep, same bank wait,
    same room check, plus a [DCO_PROBE] arm=std line carrying the actual delay."""
    ex = _BankExec([DCO, DCO, WIN], ready=(False,))
    with _rand(_RandSpy([0.99])) as spy:
        ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "0.5"}, executor=ex, seed=3)
    pl = _probe_lines(out)
    check("std_race_won", ok and ex.calls == 3 and m._states.get(HOT, {}).get("status") == "purchased", (ok, ex.calls))
    check("std_one_line_arm_std", len(pl) == 1 and "arm=std" in pl[0], pl)
    check("std_both_sleeps_std_band", len(sl) == 2 and all(_in(s, STD_LO, STD_HI) for s in sl), sl)
    check("std_bank_wait_on_both_reposts", ex.waits == [2.0, 2.0] and
          ex.events == ["ready", "wait", "ready", "ready", "wait", "ready"], (ex.waits, ex.events))
    check("std_logged_delay_is_the_sleep", pl and abs(float(re.search(r"delay=([\d.]+)", pl[0]).group(1)) - sl[0]) < 0.006,
          (pl, sl))
    check("std_line_has_cred_waited_age", pl and re.search(r"waited=\d\.\d cred=page bank_age=na$", pl[0]), pl)
    check("std_burst_line_std_band", "re-POST 1/2 in 1.0-1.5s instead of the cold re-entry" in out, out[-700:])
    check("std_one_draw_for_the_event", spy.calls == 1, spy.calls)

    # the std arm with a banked set at t=0: no wait, cred=banked, band unchanged
    ex = _BankExec([DCO, DCO, WIN], ready=(True,), bank_age=12.0)
    with _rand(_RandSpy([0.99])):
        ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "0.5"}, executor=ex, seed=3)
    pl = _probe_lines(out)
    check("std_banked_line", len(pl) == 1 and "arm=std" in pl[0] and "cred=banked bank_age=12" in pl[0], pl)
    check("std_banked_band", all(_in(s, STD_LO, STD_HI) for s in sl) and len(sl) == 2, sl)

    # the room check precedes the draw: no room -> no burst, no draw, no line (either arm)
    for v in (0.0, 0.99):
        with _rand(_RandSpy([v])) as spy:
            ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "0.5",
                                                          "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "15"}, seed=3)
        check(f"std_no_room_no_burst_no_draw[{v}]", ok and ex.calls == 1 and "no burst re-POST, wave-first decides" in out
              and not _probe_lines(out) and spy.calls == 0, (ex.calls, spy.calls))

    # std-arm sleeps match the P=0 run's band over many draws (distribution, not just the bounds)
    lows = []
    for s in range(12):
        with _rand(_RandSpy([0.99])):
            ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "0.5"}, seed=s)
        lows.extend(sl)
    check("std_sleeps_spread_over_the_band", len(lows) == 24 and min(lows) < 1.25 and max(lows) > 1.25
          and all(_in(x, STD_LO, STD_HI) for x in lows), (min(lows), max(lows)))


def test_probe_fires_only_on_repost_1_after_fs_body():
    # one draw per event, only for re-POST #1; re-POST #2 never draws and never probes
    with _rand(_RandSpy([0.0])) as spy:
        ok, out, sl, m, ex = _race1([DCO, DCO, DCO, DCO], {"TARGET_DCO_PROBE_P": "1"}, seed=3)
    check("only1_one_draw_one_line", ok and spy.calls == 1 and len(_probe_lines(out)) == 1, (spy.calls, _probe_lines(out)))
    check("only1_repost2_std", len(sl) == 2 and _in(sl[0], PROBE_LO, PROBE_HI) and _in(sl[1], STD_LO, STD_HI), sl)
    check("only1_cap_respected", out.count("[DCO_BURST] ATC-level FAST_SELLING on") == 2
          and "re-POST 3/" not in out and "cap 2 spent" in out and ex.calls == 3, (ex.calls, out[-500:]))
    # a first-shot edge 429 / 401 / edge-key DCO body: no burst, no draw, no line
    for tag, script in (("edge", [EDGE, WIN]), ("401", [A401, WIN]), ("edge_key_body", [DCO_EDGE_BODY, WIN])):
        with _rand(_RandSpy([0.0])) as spy:
            ok, out, sl, m, ex = _race1(script, {"TARGET_DCO_PROBE_P": "1"}, seed=3)
        check(f"only1_{tag}_no_probe", ok and ex.calls == 1 and not _probe_lines(out) and spy.calls == 0
              and "[DCO_BURST] ATC" not in out, (ex.calls, spy.calls, out[-300:]))
    # ANY_BODY=1 restores the looser body test, and the probe follows it (same gate, not a new one)
    ok, out, sl, m, ex = _race1([DCO_EDGE_BODY, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_BURST_ANY_BODY": "1"}, seed=3)
    check("only1_any_body_flag_follows_burst", ok and len(_probe_lines(out)) == 1 and ex.calls == 2, (ex.calls, out[-300:]))
    # burst master switch off: no burst, no probe
    with _rand(_RandSpy([0.0])) as spy:
        ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_BURST": "0"}, seed=3)
    check("only1_burst_off_no_probe", ok and not _probe_lines(out) and spy.calls == 0 and ex.calls == 1, (ex.calls, spy.calls))
    # an edge 429 / 401 DURING the burst ends it through the unchanged wave-first branch: the probe
    # was only re-POST #1; nothing probes after it
    for tag, script, wf in (("edge", [DCO, EDGE, WIN], "[WAVE_FIRST] ATC-level edge on"),
                            ("401", [DCO, A401, WIN], "[WAVE_FIRST] ATC-level auth401 on")):
        ok, out, sl, m, ex = _race1(script, {"TARGET_DCO_PROBE_P": "1"}, seed=3)
        check(f"only1_{tag}_after_probe_ends_burst", ok and ex.calls == 2 and len(_probe_lines(out)) == 1 and wf in out
              and len(sl) == 1 and _in(sl[0], PROBE_LO, PROBE_HI), (ex.calls, sl, out[-400:]))
    # cap 1: the probe is the whole burst, the next DCO takes wave-first
    ok, out, sl, m, ex = _race1([DCO, DCO, DCO], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_BURST_MAX": "1"}, seed=3)
    check("only1_cap1_probe_is_the_burst", ok and ex.calls == 2 and len(_probe_lines(out)) == 1 and "cap 1 spent" in out
          and len(sl) == 1, (ex.calls, sl, out[-400:]))


def test_per_thread_independent_draws():
    """Three racers, each its own DCO event: one draw and one [DCO_PROBE] line each, labelled by
    ident (the std arm is the concurrent control on the same drop)."""
    ws = [H._Worker(1, "primary", [DCO, DCO, WIN]), H._Worker(2, "business", [DCO, DCO, WIN]),
          H._Worker(3, "alt-1", [DCO, DCO, WIN])]
    m = H.race_mgr(ws)
    with _rand(_RandSpy([0.0, 0.99, 0.0])) as spy, env(**{**MR.FLAGS, **BASE_ENV, "TARGET_DCO_PROBE_P": "0.5"}), \
            FC._fake_sleep():
        ok, out = quiet(H._race, m, HOT)
    pl = _probe_lines(out)
    check("threads_finished", ok and [w.purchase_executor.calls for w in ws] == [3, 3, 3], m.recorded)
    check("threads_three_lines_three_draws", len(pl) == 3 and spy.calls == 3, (len(pl), spy.calls))
    arms = {re.search(r"ident=(\S+)", l).group(1): re.search(r"arm=(\w+)", l).group(1) for l in pl}
    check("threads_arms_by_ident", set(arms) == {"W1/primary", "W2/business", "W3/alt-1"}
          and sorted(arms.values()) == ["probe", "probe", "std"], arms)


def test_nofire_when_bank_required():
    """REQUIRE_BANK=1: the probe arm still never fires page-signed. It does not WAIT either --
    no re-POST, wave-first decides -- and logs nofire so the readout keeps its denominator."""
    ex = _BankExec([DCO, WIN], ready=(False,))
    ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_BURST_REQUIRE_BANK": "1"},
                                executor=ex, seed=3)
    pl = _probe_lines(out)
    check("nofire_probe_no_wait_no_repost", ok and ex.calls == 1 and ex.waits == [] and ex.events == ["ready"], (ex.calls, ex.events))
    check("nofire_probe_line", len(pl) == 1 and "arm=probe nofire=no_bank" in pl[0] and "delay=" not in pl[0], pl)
    check("nofire_std_message_unchanged", "no replayable banked set within 0s on" in out
          and "no page-signed re-POST, wave-first decides" in out, out[-600:])
    check("nofire_wave_first_took_it", "[WAVE_FIRST] ATC-level dco on" in out, out[-500:])
    # the std arm under the same requirement waits for the bank (unchanged) and logs nofire too
    ex = _BankExec([DCO, WIN], ready=(False,))
    with _rand(_RandSpy([0.99])):
        ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "0.5", "TARGET_ATC_DCO_BURST_REQUIRE_BANK": "1"},
                                    executor=ex, seed=3)
    pl = _probe_lines(out)
    check("nofire_std_waits_then_gives_up", ok and ex.calls == 1 and ex.waits == [2.0], (ex.calls, ex.waits))
    check("nofire_std_line", len(pl) == 1 and "arm=std nofire=no_bank" in pl[0], pl)
    # a banked set at t=0 under REQUIRE_BANK=1 lets the probe fire
    ex = _BankExec([DCO, WIN], ready=(True,), bank_age=8.0)
    ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1", "TARGET_ATC_DCO_BURST_REQUIRE_BANK": "1"},
                                executor=ex, seed=3)
    check("nofire_banked_probe_fires", ok and ex.calls == 2 and "arm=probe delay=" in "".join(_probe_lines(out)), (ex.calls, out[-300:]))
    # stuck harvest + STUCK_NOWAIT: the probe skips the stuck probe too (no wait is already its rule)
    check("nofire_no_stuck_line_on_probe", "harvest stuck" not in out)


# --------------------------------------------------------------------------- 5. cart-hold safety (F6)

def test_cart_hold_forced_after_probe():
    """F6 kept: the probe re-POST fires inside the cart-hold interval, so the manager resets the
    executor's interval stamp just before it; the REAL _check_cart_hold then reads the cart after
    the probe's answer, and a silently held probe add goes to the won-cart path (the shot returns
    a success) with NO further add on the line."""
    HIT = {"ok": True, "tcins": [HOT]}
    MISS = {"ok": True, "tcins": []}
    ex = _HoldExec([DCO, DCO, DCO, DCO], [MISS, HIT])
    ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1"}, executor=ex, seed=3)
    check("hold_probe_silent_hold_won_cart", ok and ex.calls == 2 and ex.tab.n == 2 and ex.hold_results == [False, True]
          and m._states.get(HOT, {}).get("status") == "purchased", (ex.calls, ex.tab.n, ex.hold_results))
    check("hold_probe_stamp_reset_before_probe", ex.ts_at_entry[0] == 0.0 and ex.ts_at_entry[1] == 0.0, ex.ts_at_entry)
    check("hold_probe_no_second_add", ex.calls == 2 and "re-POST 2/" not in out, (ex.calls, out[-400:]))
    check("hold_probe_cart_hold_hit_logged", "[CART_HOLD] %s: ATC denied (429) but the item IS in the cart" % HOT in out, out[-500:])

    # the probe's add did NOT land: the forced read misses, and re-POST #2 (std) goes on as before
    ex = _HoldExec([DCO, DCO, DCO, WIN], [MISS, MISS, HIT])
    ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1"}, executor=ex, seed=3)
    check("hold_probe_miss_then_std_repost", ok and ex.hold_results[:2] == [False, False] and ex.calls >= 3, (ex.calls, ex.hold_results))
    check("hold_probe_two_reads_even_inside_interval", ex.tab.n >= 2, ex.tab.n)

    # MUTATION: without the forced reset the probe's hold read is interval-skipped (the F6 hole), the
    # silently held add goes unseen and the loop stacks re-POST #2 on it
    ex = _HoldExec([DCO, DCO, DCO, DCO], [MISS, HIT])
    with _patched("_dco_probe_force_hold", lambda e: False):
        ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1"}, executor=ex, seed=3)
    check("hold_mutation_skips_the_read_and_stacks", ex.tab.n == 1 and ex.calls >= 3 and ex.hold_results[:2] == [False, False],
          (ex.tab.n, ex.calls, ex.hold_results))

    # the reset is applied exactly once, just before the probe shot -- never for the std arm / P=0
    calls = []
    real = bpm._dco_probe_force_hold

    def _spy(e):
        calls.append(e)
        return real(e)
    ex = _HoldExec([DCO, DCO, WIN], [MISS])
    with _patched("_dco_probe_force_hold", _spy):
        ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "1"}, executor=ex, seed=3)
    check("hold_force_once_for_probe", len(calls) == 1 and calls[0] is ex, len(calls))
    calls.clear()
    ex = _HoldExec([DCO, DCO, WIN], [MISS])
    with _patched("_dco_probe_force_hold", _spy), _rand(_RandSpy([0.99])):
        ok, out, sl, m, ex = _race1([DCO], {"TARGET_DCO_PROBE_P": "0.5"}, executor=ex, seed=3)
    check("hold_force_never_for_std_arm", ok and calls == [] and ex.ts_at_entry[1] != 0.0, (len(calls), ex.ts_at_entry))
    calls.clear()
    ex = _HoldExec([DCO, DCO, WIN], [MISS])
    with _patched("_dco_probe_force_hold", _spy):
        ok, out, sl, m, ex = _race1([DCO], {}, executor=ex, seed=3)
    check("hold_force_never_when_off", ok and calls == [], len(calls))

    # an executor without the stamp (older build / plain stub) is simply not forced -- no error
    ok, out, sl, m, ex = _race1([DCO, DCO, WIN], {"TARGET_DCO_PROBE_P": "1"}, seed=3)
    check("hold_executor_without_stamp_ok", ok and ex.calls == 3 and len(_probe_lines(out)) == 1, (ex.calls, out[-300:]))

    # source: the force sits after the submit-ts stamp and before the execute_purchase submit
    src = Path(bpm.__file__).read_text(encoding="utf-8")
    i = src.find("_dco_probe_force_hold(target_purchase_executor)")
    s = src.find("target_purchase_executor._mgr_submit_ts = time.time()")
    f = src.find("target_purchase_executor.execute_purchase(tcin, quantity=qty)", s)
    check("hold_src_force_before_submit", 0 < s < i < f, (s, i, f))


# --------------------------------------------------------------------------- 6. as armed

def _armed(ws, extra, tcin=None):
    """One real race with the wrapper's own values (+ `extra`, applied LAST so it wins) on
    per-thread virtual time; returns (ok, out, shots-by-account, gaps-by-account)."""
    tcin = tcin or FC.A
    m = H.race_mgr(ws)
    FC.MR._with_checker(m, {tcin: FC.MR._st()})
    benv = {k: v for k, v in FC._bat_env().items() if k.startswith("TARGET_")}
    benv.update(TARGET_MULTI_SKU_DISPATCH=None, TARGET_RETRY_STOP_WHEN_OOS="0")
    benv.update(extra)
    vt = FC._VirtTime()
    shots = {w.cfg.account_id: [] for w in ws}
    for w in ws:
        orig = w.purchase_executor.execute_purchase

        async def _stamped(tcin_, quantity=1, _o=orig, _a=w.cfg.account_id):
            shots[_a].append(bpm.time.time())
            return await _o(tcin_, quantity)
        w.purchase_executor.execute_purchase = _stamped
    saved = bpm.time
    bpm.time = vt
    try:
        with env(**{**MR.FLAGS, **benv}):
            ok, out = quiet(H._race, m, tcin)
    finally:
        bpm.time = saved
    gaps = {a: [b - x for x, b in zip(ts, ts[1:])] for a, ts in shots.items()}
    return ok, out, shots, gaps


def test_armed_combination_with_probe():
    """The wrapper as armed (IDENTS=all, MAX 40, REQUIRE_BANK=0, BANK_WAIT_S 2, STUCK_NOWAIT=1, 110 s
    budget) with P=0.5 and P=1: a full stub race, no exception, the burst cap respected, the probe
    on the FS racer only, in-band."""
    benv = FC._bat_env()
    # 2026-10-22: the bat carries IDENTS=0 (fast cadence reverted, C-1022-01); the combination below still runs with
    # IDENTS=all passed explicitly so the probe x fast-cadence code path stays covered.
    check("armed_bat_values", benv.get("TARGET_FAST_CADENCE_IDENTS") in ("all", "0") and benv.get("TARGET_ATC_DCO_BURST") == "1"
          and benv.get("TARGET_ATC_DCO_BURST_REQUIRE_BANK") == "0" and benv.get("TARGET_ATC_DCO_BURST_BANK_WAIT_S") == "2"
          and benv.get("TARGET_ATC_DCO_BURST_STUCK_NOWAIT") == "1" and benv.get("TARGET_RETRY_WHILE_IN_STOCK_MAX") == "40",
          {k: benv.get(k) for k in ("TARGET_FAST_CADENCE_IDENTS", "TARGET_ATC_DCO_BURST_REQUIRE_BANK",
                                    "TARGET_ATC_DCO_BURST_BANK_WAIT_S", "TARGET_ATC_DCO_BURST_STUCK_NOWAIT")})
    cap = int(benv.get("TARGET_ATC_DCO_BURST_MAX", "2"))
    for p in ("1", "0.5"):
        ws = FC._fleet(alt_script=[DCO])
        ok, out, shots, gaps = _armed(ws, {"TARGET_DCO_PROBE_P": p, "TARGET_FAST_CADENCE_IDENTS": "all"})
        pl = _probe_lines(out)
        bursts = [l for l in out.splitlines() if l.startswith("[DCO_BURST] ATC-level FAST_SELLING") and "alt-1" in l]
        check(f"armed_p{p}_finished_no_exception", ok and "Traceback" not in out and "[ERROR]" not in out, out[-500:])
        check(f"armed_p{p}_burst_cap_respected", 1 <= len(bursts) <= cap and not any(f"re-POST {cap + 1}/" in b for b in bursts)
              and f"cap {cap} spent" in out, (len(bursts), cap))
        check(f"armed_p{p}_one_probe_line_alt1_only", len(pl) == 1 and "ident=W3/alt-1" in pl[0], pl)
        arm = re.search(r"arm=(\w+)", pl[0]).group(1) if pl else ""
        d = float(re.search(r"delay=([\d.]+)", pl[0]).group(1)) if pl else -1
        ga = gaps["alt-1"]
        if p == "1":
            check("armed_p1_arm_probe", arm == "probe", pl)
        if arm == "probe":
            check(f"armed_p{p}_probe_delay_in_band", _in(d, PROBE_LO, PROBE_HI), d)
            check(f"armed_p{p}_probe_gap_is_the_delay", ga and abs(ga[0] - d) < 0.5, (ga[:2], d))
        else:
            check(f"armed_p{p}_std_delay_in_band", _in(d, STD_LO, STD_HI), d)
        check(f"armed_p{p}_rest_unchanged_fast_band", len(ga) > 3 and all(FC.FAST[0] - 0.01 <= g <= FC.FAST[1] + 0.6 for g in ga[2:]),
              (min(ga[2:] or [0]), max(ga[2:] or [0])))
        check(f"armed_p{p}_other_accounts_untouched", len(shots["primary"]) >= 22 and len(shots["business"]) >= 22
              and "cold re-entry in" not in out, {a: len(v) for a, v in shots.items()})
    # P off in the same combination: no line, the burst as before
    ws = FC._fleet(alt_script=[DCO])
    ok, out, shots, gaps = _armed(ws, {"TARGET_DCO_PROBE_P": "0", "TARGET_FAST_CADENCE_IDENTS": "all"})
    check("armed_off_no_probe_line", ok and not _probe_lines(out) and gaps["alt-1"] and all(g >= 0.9 for g in gaps["alt-1"][:2]),
          (gaps["alt-1"][:3], out[-300:]))


# --------------------------------------------------------------------------- 7. source / wiring

def test_source_wiring():
    src = Path(bpm.__file__).read_text(encoding="utf-8")
    check("src_env_read_once", src.count("env.get('TARGET_DCO_PROBE_P'") == 1 and src.count("_dco_probe = _dco_probe_cfg()") == 1)
    check("src_guarded_read", "except Exception:\n                        _dco_probe = {'p': 0.0, 'lo': 0.15, 'hi': 0.40}" in src)
    check("src_draw_only_for_repost_1_when_p_positive", "if _dco_burst_n == 0 and _dco_probe['p'] > 0.0:" in src)
    check("src_probe_skips_wait", "_burst_wait, _burst_stuck = 0.0, ''" in src
          and "_burst_wait, _burst_stuck = _dco_burst_wait_s(_dco_burst, target_purchase_executor)" in src)
    check("src_require_bank_gate_kept", "if _dco_burst['require_bank'] and not _bank_ready:" in src)
    check("src_room_check_kept", "_room < _dco_burst['hi'] + _dco_burst['bank_wait_s'] + 20.0" in src)
    check("src_std_cadence_kept", "_atc_lo = max(_hold_iv, _dco_burst['lo'] - _dco_burst_waited)" in src
          and "_atc_hi = max(_atc_lo, _dco_burst['hi'] - _dco_burst_waited)" in src
          and "_hold_iv = max(0.0, min(_hold_iv, _dco_burst['hi']))" in src)
    check("src_probe_cadence_branch_first", src.find("if _dco_burst_take and _dco_probe_label == 'probe':")
          < src.find("elif _dco_burst_take:\n                            # DCO burst cadence wins"))
    check("src_single_std_sleep_site_kept", src.count("time.sleep(random.uniform(_atc_lo, _atc_hi))") == 1)
    check("src_re_post2_untouched_dco_body_gate", "_dco_burst['on'] and _dco_body" in src
          and "'DCO_RATE_LIMITED' in _gb" in src)
    # comment corrections (research evaluator, design correction 5): the 1 s floor is an inference
    check("src_floor_comments_corrected", "documented ~1s per-IP ban" not in src and "explicit\n                        # ~1s per-IP" not in src
          and src.count("NOT a documented per-IP ban floor") >= 2 and "not a documented ban floor" in src)
    suite = (ROOT / "tests" / "run_offline_suite.py").read_text(encoding="utf-8")
    check("src_listed_in_offline_suite", '"test_dco_probe_1015"' in suite)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    n_set = len(re.findall(r"(?im)^\s*set\s+\"?TARGET_DCO_PROBE_P=", bat))
    check("src_bat_sets_probe_at_most_once", n_set <= 1, n_set)
    for ln in bat.splitlines():
        m_ = re.match(r"(?i)^\s*set\s+\"?TARGET_DCO_PROBE_P=([^\"\r\n]*)", ln)
        if m_:
            check("src_bat_probe_value_sane", 0.0 <= float(m_.group(1).strip() or 0) <= 1.0, ln)


def main():
    tests = [test_cfg_parsing, test_helpers, test_flag_off_is_byte_identical, test_probe_arm_p1,
             test_probe_with_dco_as_edge_and_fast_cadence_and_yield, test_std_arm_unchanged,
             test_probe_fires_only_on_repost_1_after_fs_body, test_per_thread_independent_draws,
             test_nofire_when_bank_required, test_cart_hold_forced_after_probe,
             test_armed_combination_with_probe, test_source_wiring]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            H.FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

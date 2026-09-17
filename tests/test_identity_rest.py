#!/usr/bin/env python3
"""Hot-sku 0916 plan P5 (U1 option c): per-account hot-TCIN park.

WHY: on 09-16 alt-1 (Bright Data 168.158.x) drew 61/181 carts-401s and 0/120
limiter passes on the hot TCINs, and every one of its shots added own-volume to
Target's per-TCIN limiter that home-IP primary (the only hot-SKU converter)
has to pass. The lead chose U1 = (c): park alt-1 on the hot TCINs while it keeps
racing regular SKUs.

TARGET_PARK_ACCOUNT_TCINS = 'acct:tcin,tcin;acct2:tcin' (cmd-safe: no pipes).
A parked (account, TCIN) sits out at the top of the race thread's retry loop
with reason 'account_parked_hot': nothing fires, the other identities race
normally, the identity tracker does not record it. Empty/unset = nobody parked
(prior behaviour). Malformed = nobody parked + one loud [PARK] line.

This file is also the plan's home for the HS-1 / BG-1 / ID-1 enforcement tests
(built in review round R1, 2026-09-17, NOT armed), the R1 pre-dispatch /
headline-reason fixes and the AC-1 latch persistence across a relaunch.

Offline: no browser, no network. Stub workers only.
Run: python tests/test_identity_rest.py
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_PARK = "TARGET_PARK_ACCOUNT_TCINS"
for _k in (_PARK, "TARGET_AMBIGUOUS_COMMIT_LATCH", "TARGET_AMBIGUOUS_COMMIT_LATCH_S",
           "TARGET_EXPOSURE_LOG", "TARGET_IDENT_CENSUS", "TARGET_IDENTITY_REST"):
    os.environ.pop(_k, None)

import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402
from src.purchasing import identity_rest as ident_rest  # noqa: E402

BAT_PATH = ROOT / "run_bot_with_nightly_restart.bat"

# The lead's U1=(c) hot list (2026-09-17): the 30th Celebration set + the other
# hot SKUs armed for the 09-16 drop.
HOT = ("1010892078,1010892076,1010892069,1010892067,1010892068,1010892065,"
       "1012422107,1011407490,1010892075,1010892071,1012055696,1011960739,1011209279")
HOT_SET = frozenset(HOT.split(","))
BAT_PARK_LINE = f'set "{_PARK}=alt-1:{HOT}"'
HOT_TCIN = "1010892069"      # the 09-16 Tin (the night's only 201)
REG_TCIN = "95290385"        # a regular SKU alt-1 keeps racing

PASSED: list = []
FAILED: list = []
_TMP = tempfile.mkdtemp(prefix="park_")


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


def park(value):
    return env(**{_PARK: value})


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


# ─────────────────────────────── 1. parser ───────────────────────────────

def test_parse_valid():
    m = bpm_mod._park_parse(f"alt-1:{HOT}")
    check("parse_lead_value_accounts", set(m) == {"alt-1"}, m)
    check("parse_lead_value_tcins", m.get("alt-1") == HOT_SET, m)
    check("parse_lead_value_13", len(m.get("alt-1", ())) == 13, m)

    m = bpm_mod._park_parse(" Alt-1 : 111 , 222 ; business:333 ; alt-1:444 ;")
    check("parse_case_whitespace_merge",
          m == {"alt-1": frozenset({"111", "222", "444"}), "business": frozenset({"333"})}, m)
    m = bpm_mod._park_parse("alt-1:111,,222,")
    check("parse_empty_tcin_items_ignored", m == {"alt-1": frozenset({"111", "222"})}, m)
    for raw in ("", "   ", None, ";", " ; ; "):
        check(f"parse_empty[{raw!r}]", bpm_mod._park_parse(raw) == {}, bpm_mod._park_parse(raw))


def test_parse_malformed():
    bad = ("alt-1", "alt-1=111|222", ":111", "alt-1:", "alt-1: , ",
           "alt-1:111|222", "alt-1:111;business", "alt-1:12a", "alt-1:111 222")
    for raw in bad:
        try:
            bpm_mod._park_parse(raw)
            check(f"parse_malformed_raises[{raw!r}]", False, "no ValueError")
        except ValueError:
            check(f"parse_malformed_raises[{raw!r}]", True)


def test_map_cache_and_malformed_log():
    with park("alt-1=111|222"):              # the plan's original pipe format: rejected
        m, out = quiet(bpm_mod._park_map)
        check("malformed_map_empty", m == {}, m)
        check("malformed_logged", "[PARK] TARGET_PARK_ACCOUNT_TCINS is malformed" in out
              and "no account is parked" in out, out)
        m2, out2 = quiet(bpm_mod._park_map)
        check("malformed_logged_once", out2 == "", out2)
        check("malformed_hit_false", bpm_mod._park_hit("alt-1", "111") is False)
    with park("alt-1:111"):
        m, out = quiet(bpm_mod._park_map)
        check("reparse_on_change", m == {"alt-1": frozenset({"111"})}, m)
        check("valid_no_log", out == "", out)
    with park(None):
        check("unset_map_empty", bpm_mod._park_map() == {})


# ─────────────────────────────── 2. hit / skip reason ───────────────────────────────

def bare_mgr():
    m = object.__new__(BulletproofPurchaseManager)
    m._ac_latch = {}
    m._ac_latch_lock = threading.Lock()
    m._ac_skip_log_ts = {}
    m._ac_error_log_path = os.path.join(tempfile.mkdtemp(dir=_TMP), "error_log.txt")
    return m


def test_hit_and_skip_reason():
    m = bare_mgr()
    with park(None):
        check("default_nobody_parked", m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == "")
    with park(""):
        check("empty_nobody_parked", m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == "")
    with park(f"alt-1:{HOT}"):
        for t in sorted(HOT_SET):
            if m._thread_skip_reason("W3/alt-1", "alt-1", t) != "account_parked_hot":
                check(f"alt1_parked[{t}]", False)
                break
        else:
            check("alt1_parked_on_all_13", True)
        check("alt1_case_insensitive", bpm_mod._park_hit("ALT-1", HOT_TCIN))
        check("alt1_int_tcin", bpm_mod._park_hit("alt-1", int(HOT_TCIN)))
        check("alt1_regular_sku_free", m._thread_skip_reason("W3/alt-1", "alt-1", REG_TCIN) == "")
        check("primary_never_parked", m._thread_skip_reason("W1/primary", "primary", HOT_TCIN) == "")
        check("business_never_parked", m._thread_skip_reason("W2/business", "business", HOT_TCIN) == "")
        check("legacy_empty_acct_free", m._thread_skip_reason("legacy", "", HOT_TCIN) == "")
        check("none_acct_free", bpm_mod._park_hit(None, HOT_TCIN) is False)
        # AC-1 latch keeps precedence (the stronger, order-history reason).
        with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1"):
            m._ac_latch[("W3/alt-1", HOT_TCIN)] = time.time()
            check("latch_precedes_park",
                  m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == "ambiguous_commit_latched")
            m._ac_latch.clear()
            check("park_with_latch_flag_on",
                  m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == "account_parked_hot")


def test_hit_never_raises():
    orig = bpm_mod._park_map
    try:
        def boom():
            raise RuntimeError("x")
        bpm_mod._park_map = boom
        check("hit_swallows_errors", bpm_mod._park_hit("alt-1", HOT_TCIN) is False)
        check("banner_swallows_errors", bpm_mod._park_banner() == "")
    finally:
        bpm_mod._park_map = orig


def test_reason_classification():
    check("tracker_not_recorded",
          ident_rest.classify_result({"success": False, "reason": "account_parked_hot"}) is None)
    terminal = ("oos", "out_of_stock", "sold_out", "reservation", "unavailable")
    check("reason_not_terminal_token", not any(t in "account_parked_hot" for t in terminal))
    check("reason_digit_free", not any(c.isdigit() for c in "account_parked_hot"))


def test_banner():
    with park(None):
        check("banner_empty_when_unset", bpm_mod._park_banner() == "")
    with park(f"alt-1:{HOT}"):
        b = bpm_mod._park_banner()
        check("banner_text", b.startswith("[PARK] TARGET_PARK_ACCOUNT_TCINS: alt-1 on 13 TCIN(s)")
              and "account_parked_hot" in b, b)


def test_banner_printed_at_init():
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    i = src.find("self._ac_skip_log_ts: Dict[str, float] = {}")
    j = src.find("self._bank_gate_timeouts: Dict[str, int] = {}", i)
    region = src[i:j] if (i >= 0 and j > i) else ""
    check("init_prints_banner", "_pb = _park_banner()" in region and "print(_pb)" in region, region[:300])
    check("init_prints_guards_banner", "_gb = _guards_banner()" in region and "print(_gb)" in region,
          region[:300])


# ─────────────────────────────── 3. race thread ───────────────────────────────

class _Cfg:
    def __init__(self, wid, acct):
        self.worker_id = wid
        self.account_id = acct


class _WSM:
    browser = object()
    session_active = True

    def set_purchase_in_progress(self, v):
        pass

    async def refresh_session(self):
        return True


class _Exec:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self._won_cart_ride_until = 0.0

    async def execute_purchase(self, tcin, quantity=1):
        self.calls += 1
        r = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return dict(r, tcin=tcin)

    async def warm_shape_headers(self, force_fresh=False):
        return True


class _Worker:
    def __init__(self, wid, acct, script):
        self.cfg = _Cfg(wid, acct)
        self.session_manager = _WSM()
        self.purchase_executor = _Exec(script)

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

    def run_async(self, coro):
        f = concurrent.futures.Future()
        try:
            f.set_result(asyncio.run(coro))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f


class _Pool:
    def __init__(self, workers):
        self._w = list(workers)

    def ready_workers(self):
        return list(self._w)

    @property
    def primary(self):
        return self._w[0]

    @property
    def workers(self):
        return list(self._w)

    def acquire_for_tcin(self, tcin):
        return self._w[0]


def race_mgr(workers):
    m = bare_mgr()
    m._state_lock = threading.RLock()
    m._states = {}
    m._load_states_unsafe = lambda: {k: dict(v) for k, v in m._states.items()}

    def _save(states):
        m._states = {k: dict(v) for k, v in states.items()}
    m._save_states_unsafe = _save
    m._active_purchases = {}
    m._purchase_tee = None
    m.status_callback = None
    m.session_initialized = True
    m.worker_pool = _Pool(workers)
    m.worker = workers[0]
    m.session_manager = workers[0].session_manager
    m.purchase_executor = workers[0].purchase_executor
    m.recorded = []
    m.done = threading.Event()
    real_update = m._update_purchase_result

    def _upd(tcin, result, race_agg=None, worker_label=None):
        m.recorded.append((worker_label, dict(result)))
        real_update(tcin, result, race_agg=race_agg, worker_label=worker_label)
        if len(m.recorded) >= len(workers):
            m.done.set()
    m._update_purchase_result = _upd
    return m


FAST_RETRY = dict(TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0",
                  TARGET_RETRY_WARM="0", TARGET_WAVE_FIRST_ONLY="0", TARGET_SHOT_BANK_GATE="0",
                  TARGET_401_PULSE="0", TARGET_RACE_ALL_WORKERS="1",
                  TARGET_RETRY_WHILE_IN_STOCK="1", TARGET_RETRY_WHILE_IN_STOCK_MAX="1")


def _race(m, tcin):
    m._start_real_purchase(tcin, "Tin", {}, max_qty=1)
    ok = m.done.wait(20)
    time.sleep(0.2)
    return ok


def _fleet():
    fail = [{"success": False, "reason": "rate_limited_429"}]
    return [_Worker(1, "primary", fail), _Worker(2, "business", fail), _Worker(3, "alt-1", fail)]


def test_race_parked_account_sits_out():
    ws = _fleet()
    m = race_mgr(ws)
    with env(**FAST_RETRY), park(f"alt-1:{HOT}"):
        ok, out = quiet(_race, m, HOT_TCIN)
    check("race_finished", ok, m.recorded)
    rec = dict(m.recorded)
    check("alt1_reason_recorded", rec.get("W3/alt-1", {}).get("reason") == "account_parked_hot", rec)
    check("alt1_nothing_fired", ws[2].purchase_executor.calls == 0, ws[2].purchase_executor.calls)
    check("primary_fired", ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("business_fired", ws[1].purchase_executor.calls == 1, ws[1].purchase_executor.calls)
    check("sits_out_log", f"W3/alt-1 sits out {HOT_TCIN}: account_parked_hot — nothing fired" in out,
          out[-500:])
    br = m._states.get(HOT_TCIN, {}).get("race_breakdown", {})
    check("breakdown_reason", br.get("W3/alt-1") == "account_parked_hot", br)
    check("race_final_failed", m._states.get(HOT_TCIN, {}).get("status") == "failed",
          m._states.get(HOT_TCIN))


def test_race_parked_account_races_regular_sku():
    ws = _fleet()
    m = race_mgr(ws)
    with env(**FAST_RETRY), park(f"alt-1:{HOT}"):
        ok, out = quiet(_race, m, REG_TCIN)
    check("regular_race_finished", ok, m.recorded)
    check("alt1_fires_regular", ws[2].purchase_executor.calls == 1, ws[2].purchase_executor.calls)
    check("regular_no_sits_out", "sits out" not in out, out[-400:])


def test_race_flag_off_everyone_fires():
    ws = _fleet()
    m = race_mgr(ws)
    with env(**FAST_RETRY), park(None):
        ok, out = quiet(_race, m, HOT_TCIN)
    check("off_race_finished", ok, m.recorded)
    check("off_all_fired", [w.purchase_executor.calls for w in ws] == [1, 1, 1],
          [w.purchase_executor.calls for w in ws])
    check("off_no_park_output", "[PARK]" not in out and "sits out" not in out, out[-400:])


def test_parked_win_elsewhere_still_purchased():
    fail = [{"success": False, "reason": "rate_limited_429"}]
    win = [{"success": True, "order_number": "X1", "quantity": 1}]
    ws = [_Worker(1, "primary", win), _Worker(2, "business", fail), _Worker(3, "alt-1", fail)]
    m = race_mgr(ws)
    with env(**FAST_RETRY), park(f"alt-1:{HOT}"):
        ok, _ = quiet(_race, m, HOT_TCIN)
    check("win_race_finished", ok, m.recorded)
    check("win_status_purchased", m._states.get(HOT_TCIN, {}).get("status") == "purchased",
          m._states.get(HOT_TCIN))


# ─────────────────────────────── 4. bat pin ───────────────────────────────

def _bat_value(name):
    """Last cmd assignment of `name` in the bat (bare or quoted set), or None."""
    val = None
    for line in BAT_PATH.read_bytes().decode("utf-8", "replace").split("\r\n"):
        s = line.strip()
        if s.lower().startswith(f'set "{name.lower()}='):
            val = s[len(f'set "{name}='):]
            if val.endswith('"'):
                val = val[:-1]
        elif s.lower().startswith(f"set {name.lower()}="):
            val = s[len(f"set {name}="):]
    return val


def test_bat_park_line():
    lines = BAT_PATH.read_bytes().decode("utf-8", "replace").split("\r\n")
    check("bat_park_line_exact", lines.count(BAT_PARK_LINE) == 1,
          [l for l in lines if _PARK in l and not l.upper().startswith("REM")])
    val = _bat_value(_PARK)
    check("bat_park_last_value", val == f"alt-1:{HOT}", val)
    try:
        m = bpm_mod._park_parse(val)
    except ValueError as e:
        m = {"error": str(e)}
    check("bat_park_parses_to_lead_list", m == {"alt-1": HOT_SET}, m)
    check("bat_park_cmd_safe", val is not None and not any(c in val for c in '|&<>^%!()"'), val)


# ───────────── 5. R1 review (2026-09-17): the S6 guards, built NOT armed ─────────────
# PC-1: HS-1 (home-share guard), BG-1 (background slow-down) and ID-1 (identity
# rest) were never delivered by stage S6. These pin the plan's P5 / P9 tests.

_S6_FLAGS = ("TARGET_IDENTITY_REST", "TARGET_IDENTITY_REST_S", "TARGET_IDENTITY_REST_K",
             "TARGET_IDENTITY_REST_M", "TARGET_IDENTITY_REST_PROXIED_ONLY",
             "TARGET_IDENTITY_REST_STAGGER", "TARGET_IDENTITY_REST_NEVER",
             "TARGET_IDENTITY_REST_RESET_GAP_S", "TARGET_HOME_SHARE_GUARD",
             "TARGET_HOME_SHARE_GUARD_GUEST", "TARGET_HOME_SHARE_GUARD_PROTECT",
             "TARGET_HOME_SHARE_GUARD_P401", "TARGET_HOME_SHARE_GUARD_TTL_S",
             "TARGET_BG_SLOW_ACCOUNTS", "TARGET_BG_SLOW_FACTOR",
             "TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST", "TARGET_AMBIGUOUS_COMMIT_LATCH_FILE")
for _k in _S6_FLAGS:
    os.environ.pop(_k, None)

REST_ON = {"TARGET_IDENTITY_REST": "1"}


def _trk(**env_kv):
    e = dict(REST_ON)
    e.update(env_kv)
    return ident_rest.IdentityTracker.from_env(e)


def _feed(trk, kinds, ident="W3/alt-1", acct="alt-1", tcin=HOT_TCIN, t0=1000.0, gap=3.0,
          proxied=True):
    out = []
    for i, k in enumerate(kinds):
        out.append(trk.record(ident, acct, tcin, k, now=t0 + i * gap, proxied=proxied))
    return out


def test_id1_trigger_k_of_m():
    t = _trk()
    snaps = _feed(t, ["auth401", "edge", "auth401"])
    r = snaps[-1].get("rest") or {}
    check("id1_401_edge_401_rests", r.get("trigger") == "2of3" and r.get("rest_s") == 150.0
          and r.get("run_shots") == 3 and "rest" not in snaps[0] and "rest" not in snaps[1], snaps)
    check("id1_is_resting", t.is_resting("W3/alt-1", HOT_TCIN, now=1010.0)
          and abs(t.rest_left("W3/alt-1", HOT_TCIN, now=1010.0) - 146.0) < 1e-6)
    check("id1_other_tcin_free", not t.is_resting("W3/alt-1", REG_TCIN, now=1010.0))
    t = _trk()
    snaps = _feed(t, ["auth401", "edge", "edge"])
    check("id1_401_edge_edge_none", not any("rest" in x for x in snaps)
          and not t.is_resting("W3/alt-1", HOT_TCIN, now=1010.0), snaps)
    t = _trk()
    snaps = _feed(t, ["auth401", "auth401"])
    check("id1_two_401_rests", "rest" in snaps[-1], snaps)
    # A trigger closes the run: the first shot after the rest is run_shots=1.
    nxt = t.record("W3/alt-1", "alt-1", HOT_TCIN, "edge", now=1003.0 + 151.0, proxied=True)
    check("id1_run_closed_after_trigger", nxt["run_shots"] == 1, nxt)
    # K=3 M=3: two 401s are not enough.
    t = _trk(TARGET_IDENTITY_REST_K="3")
    snaps = _feed(t, ["auth401", "auth401", "edge"])
    check("id1_k3_needs_three", not any("rest" in x for x in snaps), snaps)


def test_id1_resets():
    # 125 s gap (> the 120 s run reset): the second 401 opens a new run.
    t = _trk()
    a = t.record("W3/alt-1", "alt-1", HOT_TCIN, "auth401", now=1000.0, proxied=True)
    b = t.record("W3/alt-1", "alt-1", HOT_TCIN, "auth401", now=1125.0, proxied=True)
    check("id1_125s_gap_resets", "rest" not in b and b["run_shots"] == 1, (a, b))
    for closer in ("dco", "pass"):
        t = _trk()
        snaps = _feed(t, ["auth401", closer, "auth401"])
        check(f"id1_{closer}_resets", not any("rest" in x for x in snaps)
              and snaps[-1]["run_shots"] == 1, snaps)


def test_id1_eligibility():
    t = _trk()
    s = _feed(t, ["auth401", "auth401"], ident="W1/primary", acct="primary")
    check("id1_never_primary", "rest" not in s[-1], s)
    t = _trk(TARGET_IDENTITY_REST_NEVER="Business", TARGET_IDENTITY_REST_PROXIED_ONLY="0")
    s1 = _feed(t, ["auth401", "auth401"], ident="W2/business", acct="business")
    s2 = _feed(t, ["auth401", "auth401"], ident="W1/primary", acct="primary", proxied=False)
    check("id1_never_custom", "rest" not in s1[-1] and "rest" in s2[-1], (s1, s2))
    for prox in (False, None):
        t = _trk()
        s = _feed(t, ["auth401", "auth401"], proxied=prox)
        check(f"id1_proxied_only[{prox}]", "rest" not in s[-1], s)
    t = _trk(TARGET_IDENTITY_REST_PROXIED_ONLY="0")
    s = _feed(t, ["auth401", "auth401"], proxied=False)
    check("id1_proxied_only_off", "rest" in s[-1], s)


def test_id1_stagger_and_pop():
    t = _trk()
    _feed(t, ["auth401", "auth401"], ident="W3/alt-1", acct="alt-1", t0=1000.0)
    s = _feed(t, ["auth401", "auth401"], ident="W2/business", acct="business", t0=1010.0)
    r = s[-1].get("rest") or {}
    check("id1_stagger_defers_second", r.get("deferred") is True and r.get("by") == "W3/alt-1"
          and not t.is_resting("W2/business", HOT_TCIN, now=1020.0), s)
    s = _feed(t, ["auth401", "auth401"], ident="W2/business", acct="business", tcin=REG_TCIN, t0=1010.0)
    check("id1_stagger_per_tcin", "until" in (s[-1].get("rest") or {}), s)
    t = _trk(TARGET_IDENTITY_REST_STAGGER="0")
    _feed(t, ["auth401", "auth401"], ident="W3/alt-1", acct="alt-1", t0=1000.0)
    s = _feed(t, ["auth401", "auth401"], ident="W2/business", acct="business", t0=1010.0)
    check("id1_stagger_off_both_rest", t.is_resting("W2/business", HOT_TCIN, now=1020.0)
          and t.is_resting("W3/alt-1", HOT_TCIN, now=1020.0), s)
    # pop_expired: each ended rest reported exactly once.
    t = _trk()
    _feed(t, ["auth401", "auth401"], t0=1000.0)
    check("id1_pop_before_end", t.pop_expired(now=1100.0) == [])
    got = t.pop_expired(now=1160.0)
    check("id1_pop_once", len(got) == 1 and got[0][:2] == ("W3/alt-1", HOT_TCIN)
          and abs(got[0][2] - 157.0) < 1e-6 and t.pop_expired(now=1200.0) == [], got)
    check("id1_not_resting_after_pop", not t.is_resting("W3/alt-1", HOT_TCIN, now=1160.0))


def test_id1_from_env_clamps_and_flag_off():
    c = ident_rest.rest_cfg({"TARGET_IDENTITY_REST_S": "10", "TARGET_IDENTITY_REST_K": "9",
                             "TARGET_IDENTITY_REST_M": "3"})
    check("id1_clamp_low", c["rest_s"] == 125.0 and c["k"] == 3 and c["m"] == 3, c)
    c = ident_rest.rest_cfg({"TARGET_IDENTITY_REST_S": "5000", "TARGET_IDENTITY_REST_M": "99",
                             "TARGET_IDENTITY_REST_K": "x"})
    check("id1_clamp_high_and_bad", c["rest_s"] == 900.0 and c["m"] == 8 and c["k"] == 2, c)
    c = ident_rest.rest_cfg({"TARGET_IDENTITY_REST_M": "0", "TARGET_IDENTITY_REST_S": "nan"})
    check("id1_clamp_m_min_nan", c["m"] == 1 and c["k"] == 1 and c["rest_s"] == 150.0, c)
    c = ident_rest.rest_cfg({})
    check("id1_defaults", c == {"rest_s": 150.0, "k": 2, "m": 3, "proxied_only": True, "stagger": True,
                                "never": frozenset({"primary"})}, c)
    check("id1_flag_off_no_rest_cfg",
          ident_rest.IdentityTracker.from_env({"TARGET_EXPOSURE_LOG": "1"}).rest is None)
    t = ident_rest.IdentityTracker.from_env({"TARGET_EXPOSURE_LOG": "1"})
    s = _feed(t, ["auth401"] * 4)
    check("id1_flag_off_counts_but_never_rests",
          not any("rest" in x for x in s) and t.counters("W3/alt-1", HOT_TCIN)["p401"] == 4
          and not t.is_resting("W3/alt-1", HOT_TCIN, now=1005.0), s)


def test_hs1_guard_unit():
    g = ident_rest.HomeShareGuard.from_env({})
    check("hs1_defaults", (g.guest, g.protect, g.p401, g.ttl_s) == ("alt-1", "primary", 2, 3600.0),
          (g.guest, g.protect, g.p401, g.ttl_s))
    check("hs1_one_401_no_trigger", g.note("primary", HOT_TCIN, now=1000.0) is None
          and g.guest_left("alt-1", now=1000.0) == 0.0)
    trig = g.note("Primary", HOT_TCIN, now=1500.0)
    check("hs1_two_401s_trigger", trig is not None and trig["count"] == 2
          and abs(g.guest_left("alt-1", now=1500.0) - 3600.0) < 1e-6, trig)
    check("hs1_guest_any_tcin", g.guest_left("ALT-1", now=2000.0) > 0)
    check("hs1_protect_never_parked", g.guest_left("primary", now=2000.0) == 0.0
          and g.guest_left("business", now=2000.0) == 0.0 and g.guest_left("", now=2000.0) == 0.0)
    check("hs1_ttl_expiry", g.guest_left("alt-1", now=1500.0 + 3600.0 + 1) == 0.0)
    g = ident_rest.HomeShareGuard.from_env({})
    g.note("primary", HOT_TCIN, now=1000.0)
    check("hs1_window_30min", g.note("primary", HOT_TCIN, now=1000.0 + 1801.0) is None)
    g = ident_rest.HomeShareGuard.from_env({})
    g.note("primary", HOT_TCIN, now=1000.0)
    check("hs1_per_tcin", g.note("primary", REG_TCIN, now=1001.0) is None)
    check("hs1_other_account_ignored", g.note("alt-1", HOT_TCIN, now=1002.0) is None
          and g.note("business", HOT_TCIN, now=1003.0) is None)
    g = ident_rest.HomeShareGuard(guest="primary", protect="primary")
    g.note("primary", HOT_TCIN, now=1.0)
    g.note("primary", HOT_TCIN, now=2.0)
    check("hs1_misconfig_never_parks_protect", g.guest_left("primary", now=3.0) == 0.0)
    g = ident_rest.HomeShareGuard.from_env({"TARGET_HOME_SHARE_GUARD_P401": "0",
                                            "TARGET_HOME_SHARE_GUARD_TTL_S": "5"})
    check("hs1_clamps", g.p401 == 1 and g.ttl_s == 60.0, (g.p401, g.ttl_s))


def test_hs1_manager_skip_and_note():
    m = bare_mgr()
    with env(TARGET_HOME_SHARE_GUARD="1"):
        ex = type("X", (), {"_atc_px_block_seen": False})()
        _, out = quiet(bpm_mod._hs1_note, m, "W1/primary", "primary", HOT_TCIN,
                       {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401"}, ex)
        check("hs1_mgr_one_no_park", m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == ""
              and "[HOME_SHARE_GUARD]" not in out, out)
        # edge 429s and other accounts' 401s do not count
        quiet(bpm_mod._hs1_note, m, "W1/primary", "primary", HOT_TCIN,
              {"success": False, "reason": "rate_limited_429", "gate_kind": "edge"}, ex)
        quiet(bpm_mod._hs1_note, m, "W2/business", "business", HOT_TCIN,
              {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401"}, ex)
        check("hs1_mgr_still_free", m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == "")
        # a PX-block ATC 403 (no gate_kind) counts
        ex._atc_px_block_seen = True
        _, out = quiet(bpm_mod._hs1_note, m, "W1/primary", "primary", HOT_TCIN,
                       {"success": False, "reason": "atc_failed_api_mode"}, ex)
        check("hs1_mgr_px_counts_and_logs", "[HOME_SHARE_GUARD] W1/primary (primary) drew 2" in out, out)
        with open(m._ac_error_log_path, encoding="utf-8") as f:
            check("hs1_error_log_line", "[HOME_SHARE_GUARD]" in f.read())
        r, out = quiet(m._thread_skip_reason, "W3/alt-1", "alt-1", REG_TCIN)
        check("hs1_guest_skipped_all_tcins", r == "home_share_guard" and "sits out" in out, (r, out))
        check("hs1_protect_never_skipped", m._thread_skip_reason("W1/primary", "primary", HOT_TCIN) == "")
        check("hs1_business_free", m._thread_skip_reason("W2/business", "business", HOT_TCIN) == "")
        check("hs1_quiet_mode", quiet(m._thread_skip_reason, "W3/alt-1", "alt-1", HOT_TCIN, log=False)[1] == "")
    check("hs1_flag_off_no_skip", m._thread_skip_reason("W3/alt-1", "alt-1", HOT_TCIN) == "")
    m2 = bare_mgr()
    with env(TARGET_HOME_SHARE_GUARD=None):
        for _ in range(3):
            bpm_mod._hs1_note(m2, "W1/primary", "primary", HOT_TCIN,
                              {"success": False, "gate_kind": "auth401"}, None)
    check("hs1_flag_off_no_guard_built", getattr(m2, "_hs1_guard", None) is None)
    for reason in ("home_share_guard", "identity_resting"):
        check(f"s6_reason_not_recorded[{reason}]",
              ident_rest.classify_result({"success": False, "reason": reason}) is None)
        check(f"s6_reason_safe[{reason}]", not any(c.isdigit() for c in reason) and not any(
            t in reason for t in ("oos", "out_of_stock", "sold_out", "reservation", "unavailable")))


def test_bg1_factor():
    import src.session.purchase_executor as pe_mod
    f = pe_mod.bg_slow_factor
    check("bg1_unset_is_1", f("alt-1", {}) == 1.0 and f("alt-1", {"TARGET_BG_SLOW_ACCOUNTS": " "}) == 1.0)
    e = {"TARGET_BG_SLOW_ACCOUNTS": "Alt-1; business"}
    check("bg1_default_factor_2", f("alt-1", e) == 2.0 and f("BUSINESS", e) == 2.0)
    check("bg1_unlisted_1", f("primary", e) == 1.0 and f(None, e) == 1.0)
    for raw, want in (("3", 3.0), ("9", 4.0), ("0.5", 1.0), ("x", 1.0), ("nan", 1.0), ("inf", 1.0), (" 2.5 ", 2.5)):
        got = f("alt-1", dict(e, TARGET_BG_SLOW_FACTOR=raw))
        check(f"bg1_clamp[{raw}]", got == want, got)
    src = Path(pe_mod.__file__).read_text(encoding="utf-8")
    check("bg1_wired_refill", "_bgf = bg_slow_factor(getattr(self.session_manager, 'account_id', None))" in src)
    check("bg1_wired_harvest_idle", "_bgf = bg_slow_factor(self._harvest_acct())" in src)


class _ProxWSM(_WSM):
    proxy_url = "http://127.0.0.1:23003"
    _browser_launched_at = 0.0


class _ProxWorker(_Worker):
    def __init__(self, wid, acct, script):
        super().__init__(wid, acct, script)
        self.session_manager = _ProxWSM()


_A401 = {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401"}
_EDGE = {"success": False, "reason": "rate_limited_429", "gate_kind": "edge"}


def test_id1_race_thread():
    # Attempt 2's 401 starts the rest; attempt 3 stops and keeps attempt 2's result.
    ws = [_Worker(1, "primary", [_EDGE]), _ProxWorker(3, "alt-1", [_A401])]
    m = race_mgr(ws)
    with env(**dict(FAST_RETRY, TARGET_RETRY_WHILE_IN_STOCK_MAX="4"), **REST_ON):
        ok, out = quiet(_race, m, HOT_TCIN)
    rec = dict(m.recorded)
    check("id1_race_finished", ok, m.recorded)
    check("id1_race_two_shots_then_stop", ws[1].purchase_executor.calls == 2
          and rec.get("W3/alt-1", {}).get("reason") == "atc_failed_api_mode", (ws[1].purchase_executor.calls, rec))
    check("id1_race_logs", "[IDENT_REST] W3/alt-1 1010892069 trigger=2of3" in out
          and "W3/alt-1 stops re-racing 1010892069: identity_resting" in out, out[-800:])
    check("id1_race_primary_unaffected", ws[0].purchase_executor.calls == 4, ws[0].purchase_executor.calls)
    # The next dispatch while resting: attempt 1 sits out, nothing fires.
    m.recorded.clear()
    m.done.clear()
    ws[1].purchase_executor.calls = 0
    with env(**FAST_RETRY, **REST_ON):
        ok, out = quiet(_race, m, HOT_TCIN)
    rec = dict(m.recorded)
    check("id1_race_sits_out", ok and ws[1].purchase_executor.calls == 0
          and rec.get("W3/alt-1", {}).get("reason") == "identity_resting"
          and "[IDENT_REST] W3/alt-1 sits out 1010892069 (rest ends in" in out, (rec, out[-600:]))
    check("id1_headline_is_real_attempt",
          m._states.get(HOT_TCIN, {}).get("failure_reason") == "rate_limited_429",
          m._states.get(HOT_TCIN))
    # Rest over -> 'back on' once, and the identity fires again.
    trk = m._ident_tracker
    with trk._lock:
        for k in list(trk._rest_until):
            trk._rest_until[k] = time.time() - 1.0
    m.recorded.clear()
    m.done.clear()
    with env(**FAST_RETRY, **REST_ON):
        ok, out = quiet(_race, m, HOT_TCIN)
    check("id1_back_on_logged_once", out.count("[IDENT_REST] W3/alt-1 back on 1010892069 after") == 1, out[-600:])
    check("id1_fires_after_rest", ws[1].purchase_executor.calls == 1, ws[1].purchase_executor.calls)
    # Flag off: nothing rests, everyone keeps firing.
    ws = [_Worker(1, "primary", [_EDGE]), _ProxWorker(3, "alt-1", [_A401])]
    m = race_mgr(ws)
    with env(**dict(FAST_RETRY, TARGET_RETRY_WHILE_IN_STOCK_MAX="4"), TARGET_IDENTITY_REST=None,
             TARGET_EXPOSURE_LOG="1"):
        ok, out = quiet(_race, m, HOT_TCIN)
    check("id1_flag_off_race_unchanged", ok and ws[1].purchase_executor.calls == 4
          and "[IDENT_REST]" not in out, (ws[1].purchase_executor.calls, out[-400:]))


def test_id1_wave_first_break():
    # One 401 already on record -> this attempt's 401 starts a rest -> the
    # wave-first branch ends the window instead of sleeping 55-70 s.
    ws = [_ProxWorker(3, "alt-1", [_A401])]
    m = race_mgr(ws)
    with env(**dict(FAST_RETRY, TARGET_WAVE_FIRST_ONLY="1", TARGET_RETRY_WHILE_IN_STOCK_MAX="4"),
             **REST_ON):
        bpm_mod._dx_tracker_of(m).record("W3/alt-1", "alt-1", HOT_TCIN, "auth401", proxied=True)
        t0 = time.time()
        ok, out = quiet(_race, m, HOT_TCIN)
        el = time.time() - t0
    check("id1_wf_break_no_sleep", ok and el < 10.0 and ws[0].purchase_executor.calls == 1, (ok, el))
    check("id1_wf_break_log", "[IDENT_REST] W3/alt-1 1010892069: resting" in out
          and "no cold re-entry this window" in out and "cold re-entry in" not in out, out[-600:])
    # One worker = the non-race path (label None); the attempt's own result is kept.
    check("id1_wf_keeps_result", len(m.recorded) == 1
          and m.recorded[0][1].get("reason") == "atc_failed_api_mode", m.recorded)
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    i = src.find("_rest_left = _ident_rest_left(self, _skip_ident, tcin)")
    j = src.find("time.sleep(_re_s)")
    check("id1_wf_check_before_sleep", 0 < i < j, (i, j))


def test_hs1_race_guest_sits_out():
    ws = _fleet()
    m = race_mgr(ws)
    with env(**FAST_RETRY, TARGET_HOME_SHARE_GUARD="1"):
        g = bpm_mod._hs1_guard_of(m)
        g.note("primary", HOT_TCIN)
        g.note("primary", HOT_TCIN)
        ok, out = quiet(_race, m, REG_TCIN)
    rec = dict(m.recorded)
    check("hs1_race_guest_skipped", ok and ws[2].purchase_executor.calls == 0
          and rec.get("W3/alt-1", {}).get("reason") == "home_share_guard", rec)
    check("hs1_race_others_fire", ws[0].purchase_executor.calls == 1 and ws[1].purchase_executor.calls == 1)


def test_guards_banner():
    with env(TARGET_HOME_SHARE_GUARD=None, TARGET_IDENTITY_REST=None, TARGET_BG_SLOW_ACCOUNTS=None):
        check("banner_guards_empty", bpm_mod._guards_banner() == "")
    with env(TARGET_HOME_SHARE_GUARD="1", TARGET_IDENTITY_REST="1", TARGET_BG_SLOW_ACCOUNTS="alt-1"):
        b = bpm_mod._guards_banner()
    check("banner_guards_text", b.startswith("[GUARDS] HS-1 home-share guard ON")
          and "ID-1 identity rest ON (2of3 auth401 -> 150s" in b and "BG-1 background slow-down for alt-1" in b, b)


# ───────────── 6. R1 review: pre-dispatch sit-outs + headline reason ─────────────

def test_predispatch_counts_every_sit_out():
    ws = _fleet()
    m = race_mgr(ws)
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1"), park(f"alt-1:{HOT}"):
        now = time.time()
        m._ac_latch[("W1/primary", HOT_TCIN)] = now
        m._ac_latch[("W2/business", HOT_TCIN)] = now
        r, out = quiet(m._all_dispatch_candidates_latched, HOT_TCIN)
        check("predispatch_latch_plus_park", r is True and "[DISPATCH_SKIP] 1010892069" in out
              and "W3/alt-1 account_parked_hot" in out, out)
        check("predispatch_regular_sku_free", m._all_dispatch_candidates_latched(REG_TCIN) is False)
        m._ac_latch.pop(("W2/business", HOT_TCIN))
        check("predispatch_one_free", m._all_dispatch_candidates_latched(HOT_TCIN) is False)
    ws = [_Worker(3, "alt-1", [{}])]
    m = race_mgr(ws)
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH=None), park(f"alt-1:{HOT}"):
        check("predispatch_park_without_latch_flag", quiet(m._all_dispatch_candidates_latched, HOT_TCIN)[0] is True)
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH=None), park(None):
        check("predispatch_all_flags_off", m._all_dispatch_candidates_latched(HOT_TCIN) is False)
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("predispatch_call_site_ungated", "if self._all_dispatch_candidates_latched(tcin):" in src
          and "if _ac_latch_on() and self._all_dispatch_candidates_latched(tcin):" not in src)


def test_headline_failure_reason():
    ws = _fleet()
    m = race_mgr(ws)
    with env(**FAST_RETRY), park(f"alt-1:{HOT}"):
        ok, _ = quiet(_race, m, HOT_TCIN)
    check("headline_skips_sit_out", ok and m._states.get(HOT_TCIN, {}).get("failure_reason") == "rate_limited_429",
          m._states.get(HOT_TCIN))
    ws = [_Worker(3, "alt-1", [{}])]
    m = race_mgr(ws)
    with env(**FAST_RETRY), park(f"alt-1:{HOT}"):
        ok, _ = quiet(_race, m, HOT_TCIN)
    check("headline_all_sit_out_falls_back",
          ok and m._states.get(HOT_TCIN, {}).get("failure_reason") == "account_parked_hot", m._states.get(HOT_TCIN))
    for r in ("account_parked_hot", "ambiguous_commit_latched", "identity_resting",
              "home_share_guard", "held_cart_idle_skip", "held_cart_other_tcin"):
        check(f"sit_out_reason[{r}]", bpm_mod._is_sit_out_reason(r))
    for r in ("rate_limited_429", "won_cart_held", "execution_timeout", "", None):
        check(f"not_sit_out_reason[{r}]", not bpm_mod._is_sit_out_reason(r))


# ───────────── 7. R1 review (R1-AC1-1): the AC-1 latch survives a relaunch ─────────────

def test_ac_latch_file_roundtrip():
    d = tempfile.mkdtemp(dir=_TMP)
    path = os.path.join(d, "state", "ambiguous_commit_latch.json")
    now = 10_000.0
    ok = bpm_mod._ac_latch_save(path, {("W1/primary", HOT_TCIN): now - 100.0,
                                       ("W2/business", REG_TCIN): now - 5000.0}, now, 1800.0)
    check("acfile_saved", ok and os.path.exists(path) and not any(
        n.startswith("ambiguous_commit_latch.json.tmp") for n in os.listdir(os.path.dirname(path))))
    got = bpm_mod._ac_latch_load(path, now, 1800.0)
    check("acfile_roundtrip_drops_expired", got == {("W1/primary", HOT_TCIN): now - 100.0}, got)
    check("acfile_expired_on_load", bpm_mod._ac_latch_load(path, now + 1800.0, 1800.0) == {})
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("acfile_malformed_empty", bpm_mod._ac_latch_load(path, now, 1800.0) == {})
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"latches": {"no-pipe": 9999, "W1/primary|1": true, "W1/primary|2": "x", '
                '"W1/primary|3": 99999999, "|4": 9999, "W1/primary|5": 9999}}')
    check("acfile_bad_entries_skipped",
          bpm_mod._ac_latch_load(path, now, 1800.0) == {("W1/primary", "5"): 9999.0},
          bpm_mod._ac_latch_load(path, now, 1800.0))
    check("acfile_missing_empty", bpm_mod._ac_latch_load(os.path.join(d, "nope.json"), now, 1800.0) == {})


def test_ac_latch_persist_and_restore():
    d = tempfile.mkdtemp(dir=_TMP)
    path = os.path.join(d, "latch.json")
    m = bare_mgr()
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_AMBIGUOUS_COMMIT_LATCH_FILE=path):
        # A stand-in (no __init__) never touches the file.
        quiet(m._ac_latch_mark, "W1/primary", HOT_TCIN, "test")
        check("acpersist_standin_no_file", not os.path.exists(path))
        # The init-time restore points the manager at the file.
        n, _ = quiet(m._ac_restore_latches)
        check("acpersist_restore_empty", n == 0 and m._ac_latch_path == path)
        quiet(m._ac_latch_mark, "W1/primary", HOT_TCIN, "po_unresolved")
        check("acpersist_mark_writes", os.path.exists(path))
        # "Crash + relaunch": a fresh manager restores the latch.
        m2 = bare_mgr()
        n, out = quiet(m2._ac_restore_latches)
        check("acpersist_restored", n == 1 and "[AMBIGUOUS_COMMIT] restored 1 latch(es)" in out
              and "W1/primary 1010892069" in out, out)
        check("acpersist_restored_skips", m2._thread_skip_reason("W1/primary", "primary", HOT_TCIN)
              == "ambiguous_commit_latched")
        with open(m2._ac_error_log_path, encoding="utf-8") as f:
            check("acpersist_error_log", "restored 1 latch(es)" in f.read())
        with env(TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST="0"):
            m3 = bare_mgr()
            n, _ = quiet(m3._ac_restore_latches)
            check("acpersist_killswitch", n == 0 and not m3._ac_latch)
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH=None, TARGET_AMBIGUOUS_COMMIT_LATCH_FILE=path):
        m4 = bare_mgr()
        n, _ = quiet(m4._ac_restore_latches)
        check("acpersist_latch_off_no_restore", n == 0 and not m4._ac_latch)
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_AMBIGUOUS_COMMIT_LATCH_FILE=None):
        check("acpersist_default_path", bpm_mod._ac_latch_file() == os.path.join("state", "ambiguous_commit_latch.json"))
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    i = src.find("self._ac_skip_log_ts: Dict[str, float] = {}")
    j = src.find("self._bank_gate_timeouts: Dict[str, int] = {}", i)
    check("acpersist_init_calls_restore", "self._ac_restore_latches()" in src[i:j])


# ───── 8. R2 review (R2-AC1-PERSIST-RACE): simultaneous latches all persist ─────

def _latch_race_round(path, idents, tcins, jitter_s=0.0):
    """One round: len(idents) threads behind a barrier call the real
    _ac_latch_mark on ONE manager; returns what a relaunch would restore."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    m = bare_mgr()
    m._ac_latch_path = path
    bar = threading.Barrier(len(idents))
    errs = []

    def go(k):
        try:
            bar.wait(timeout=10)
            if jitter_s:
                time.sleep(((k * 7919) % 5) / 5.0 * jitter_s)
            m._ac_latch_mark(idents[k], tcins[k], "execution_timeout")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ths = [threading.Thread(target=go, args=(k,)) for k in range(len(idents))]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=20)
    got = bpm_mod._ac_latch_load(path, time.time(), 1800.0)
    return got, buf.getvalue(), errs


def test_ac_latch_concurrent_marks_all_persist():
    d = tempfile.mkdtemp(dir=_TMP)
    path = os.path.join(d, "state", "ambiguous_commit_latch.json")
    idents = ["W1/primary", "W2/business", "W3/alt-1"]
    bad, noisy, errs_all = [], 0, []
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST=None):
        for rnd in range(60):
            tcins = [HOT_TCIN] * 3 if rnd % 2 == 0 else [HOT_TCIN, REG_TCIN, "1011407490"]
            got, out, errs = _latch_race_round(path, idents, tcins, jitter_s=0.002 if rnd % 3 == 2 else 0.0)
            errs_all += errs
            if set(got) != set(zip(idents, tcins)):
                bad.append((rnd, sorted(got)))
            if "could not persist" in out:
                noisy += 1
        check("aclatch_race_every_round_restores_all", not bad, bad[:5])
        check("aclatch_race_no_persist_errors", noisy == 0, noisy)
        check("aclatch_race_threads_clean", not errs_all, errs_all[:3])
        left = [n for n in os.listdir(os.path.dirname(path)) if ".tmp" in n]
        check("aclatch_race_no_temp_files_left", not left, left)
        # Two managers-worth of rounds on one file: the newest superset wins.
        m = bare_mgr()
        m._ac_latch_path = path
        quiet(m._ac_latch_mark, "W1/primary", HOT_TCIN, "a")
        quiet(m._ac_latch_mark, "W2/business", REG_TCIN, "b")
        got = bpm_mod._ac_latch_load(path, time.time(), 1800.0)
        check("aclatch_sequential_superset",
              set(got) == {("W1/primary", HOT_TCIN), ("W2/business", REG_TCIN)}, got)
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("aclatch_save_lock_defined", "_AC_SAVE_LOCK = threading.RLock()" in src)


def test_ac_latch_save_replace_retry():
    """os.replace hitting a transient Windows PermissionError (antivirus /
    indexer handle) is retried; a persistent one fails loudly and leaves no
    temp file behind."""
    d = tempfile.mkdtemp(dir=_TMP)
    path = os.path.join(d, "latch.json")
    real_replace = os.replace
    calls = []

    def flaky(src_p, dst_p):
        calls.append(src_p)
        if len(calls) <= 2:
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(src_p, dst_p)

    os.replace = flaky
    try:
        ok, out = quiet(bpm_mod._ac_latch_save, path, {("W1/primary", HOT_TCIN): 9_000.0}, 10_000.0, 1800.0)
    finally:
        os.replace = real_replace
    check("aclatch_replace_retried_ok", ok is True and len(calls) == 3 and out == "", (ok, calls, out))
    check("aclatch_replace_retried_content",
          bpm_mod._ac_latch_load(path, 10_000.0, 1800.0) == {("W1/primary", HOT_TCIN): 9_000.0})
    check("aclatch_tmp_names_unique", len(set(calls)) == 1 and ".tmp." in calls[0]
          and calls[0].count(".") >= 4, calls)

    def always(src_p, dst_p):
        raise PermissionError(13, "denied")

    os.replace = always
    try:
        ok, out = quiet(bpm_mod._ac_latch_save, path, {("W2/business", HOT_TCIN): 9_500.0}, 10_000.0, 1800.0)
    finally:
        os.replace = real_replace
    check("aclatch_replace_fails_loudly", ok is False and "could not persist" in out, (ok, out))
    check("aclatch_replace_fail_no_temp_left", not [n for n in os.listdir(d) if ".tmp" in n], os.listdir(d))
    check("aclatch_replace_fail_keeps_old_file",
          bpm_mod._ac_latch_load(path, 10_000.0, 1800.0) == {("W1/primary", HOT_TCIN): 9_000.0})
    # Two saves never share a temp name.
    seen = []

    def spy(src_p, dst_p):
        seen.append(src_p)
        return real_replace(src_p, dst_p)

    os.replace = spy
    try:
        quiet(bpm_mod._ac_latch_save, path, {}, 10_000.0, 1800.0)
        quiet(bpm_mod._ac_latch_save, path, {}, 10_000.0, 1800.0)
    finally:
        os.replace = real_replace
    check("aclatch_tmp_name_per_call", len(seen) == 2 and seen[0] != seen[1], seen)


# ───── 9. R3 review (2026-09-17, cart safety): skip strand + latch durability ─────

class _PoolRA:
    """Pool whose ready list can differ from its worker list (a relaunching
    worker is not ready)."""

    def __init__(self, ready, all_):
        self.ready, self._a = list(ready), list(all_)

    def ready_workers(self):
        return list(self.ready)

    @property
    def primary(self):
        return self._a[0]

    @property
    def workers(self):
        return list(self._a)


class _BareW:
    def __init__(self, wid, acct):
        self.cfg = _Cfg(wid, acct)
        self.session_manager = None
        self.purchase_executor = None

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"


def _rearm_builder():
    """app.py's pure StockMonitorThread._build_level_rearm_map, extracted
    without importing app.py."""
    import ast
    import textwrap
    from datetime import datetime
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "_build_level_rearm_map":
            ns = {"datetime": datetime}
            exec(textwrap.dedent(ast.get_source_segment(src, node)), ns)
            return ns["_build_level_rearm_map"]
    raise AssertionError("_build_level_rearm_map not found in app.py")


def _strand_mgr(ready, all_):
    m = bare_mgr()
    m._state_lock = threading.RLock()
    m._states = {}
    m._load_states_unsafe = lambda: {k: dict(v) for k, v in m._states.items()}

    def _save(states):
        m._states = {k: dict(v) for k, v in states.items()}
        return True
    m._save_states_unsafe = _save
    m._active_purchases = {}
    m._warmup_cycle_counter = 5
    m._maybe_run_session_sentinel = lambda: None
    m.dispatched = []
    m.start_purchase = lambda tcin, title, max_qty=1: (m.dispatched.append(tcin)
                                                       or {"success": False, "reason": "stub"})
    m.worker_pool = _PoolRA(ready, all_)
    return m


def _stock_event(m, stock):
    """What app.py's _handle_stock_update does with one event: the stock-aware
    reset, then process_stock_data (cwd = repo root for the config read)."""
    old = os.getcwd()
    os.chdir(ROOT)
    try:
        quiet(m.reset_completed_purchases_by_stock_status, stock)
        _, out = quiet(m.process_stock_data, stock)
    finally:
        os.chdir(old)
    return out


def test_r3_dispatch_skip_keeps_tcin_rearmable():
    """R1-PARK-DISPATCH-STRANDS-LIVE-TCIN: a TCIN skipped because every ready
    identity sits out (primary AC-1 latched, alt-1 parked, business relaunching)
    stays visible to the level re-arm, and is raced once someone is free."""
    build = _rearm_builder()
    primary, business, alt1 = _BareW(1, "primary"), _BareW(2, "business"), _BareW(3, "alt-1")
    stock = {HOT_TCIN: {"in_stock": True, "max_qty": 1, "title": "Tin"}}

    def snap():
        return {HOT_TCIN: types.SimpleNamespace(in_stock=True, last_checked_at=time.time(), title="Tin",
                                                availability_status="IN_STOCK")}

    base_env = dict(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST="0",
                    TARGET_WARMUP_CYCLE_SKIP_ON_STOCK="1", TARGET_HOME_SHARE_GUARD=None,
                    TARGET_IDENTITY_REST=None, TARGET_REARM_AFTER_EMERGENCY_RESET=None)
    with env(**base_env), park(f"alt-1:{HOT}"):
        m = _strand_mgr([primary, alt1], [primary, business, alt1])
        m._states = {HOT_TCIN: {"status": "failed", "completed_at": time.time() - 20,
                                "failure_reason": "execution_timeout"}}
        m._ac_latch[("W1/primary", HOT_TCIN)] = time.time()
        check("r3k_rearm_publishes_failed", HOT_TCIN in build(snap(), m.get_all_states(), time.time()))
        out = _stock_event(m, stock)
        st = m._states.get(HOT_TCIN, {})
        check("r3k_skip_no_dispatch", m.dispatched == [] and "[DISPATCH_SKIP] 1010892069" in out
              and st.get("status") == "ready", (m.dispatched, st, out[-300:]))
        check("r3k_skip_stamps_hint", isinstance(st.get("rearm_hint_ts"), float)
              and time.time() - st["rearm_hint_ts"] < 5, st)
        check("r3k_skipped_tcin_still_rearmed", HOT_TCIN in build(snap(), m.get_all_states(), time.time()),
              m.get_all_states())
        # A second skipped tick re-stamps (the 90 s TTL stays fresh while everyone sits out).
        m._states[HOT_TCIN]["rearm_hint_ts"] = time.time() - 80.0
        _stock_event(m, stock)
        check("r3k_restamped", time.time() - m._states[HOT_TCIN]["rearm_hint_ts"] < 5, m._states[HOT_TCIN])
        # business is back: the next re-arm event dispatches the live TCIN.
        m.worker_pool.ready = [primary, business, alt1]
        rearm = build(snap(), m.get_all_states(), time.time())
        _stock_event(m, rearm)
        check("r3k_dispatched_when_free", m.dispatched == [HOT_TCIN], m.dispatched)
        # Only business / primary latched and alt-1 parked for 31 min: the latch
        # expires and the TCIN is raced without any OOS->IS edge.
        m = _strand_mgr([primary, alt1], [primary, business, alt1])
        m._states = {HOT_TCIN: {"status": "ready"}}
        m._ac_latch[("W1/primary", HOT_TCIN)] = time.time()
        _stock_event(m, stock)
        check("r3k_skip_again", m.dispatched == [] and m._states[HOT_TCIN].get("rearm_hint_ts"))
        m._ac_latch[("W1/primary", HOT_TCIN)] = time.time() - 1900.0
        rearm = build(snap(), m.get_all_states(), time.time())
        _stock_event(m, rearm)
        check("r3k_dispatched_after_latch_expiry", HOT_TCIN in rearm and m.dispatched == [HOT_TCIN],
              (rearm, m.dispatched))
        # Kill-switch: no stamp (the pre-R3 behaviour).
        with env(TARGET_REARM_AFTER_EMERGENCY_RESET="0"):
            m = _strand_mgr([primary, alt1], [primary, business, alt1])
            m._states = {HOT_TCIN: {"status": "ready"}}
            m._ac_latch[("W1/primary", HOT_TCIN)] = time.time()
            _stock_event(m, stock)
            check("r3k_killswitch_no_hint", "rearm_hint_ts" not in m._states[HOT_TCIN], m._states)
        # A regular SKU (nobody sits out) is dispatched and never stamped.
        m = _strand_mgr([primary, alt1], [primary, business, alt1])
        m._states = {REG_TCIN: {"status": "ready"}}
        m._ac_latch[("W1/primary", HOT_TCIN)] = time.time()
        _stock_event(m, {REG_TCIN: {"in_stock": True, "max_qty": 1, "title": "Reg"}})
        check("r3k_regular_dispatched", m.dispatched == [REG_TCIN]
              and "rearm_hint_ts" not in m._states.get(REG_TCIN, {}), m._states)
    # Unit: never raises, never touches a non-ready row.
    m = _strand_mgr([], [])
    states = {HOT_TCIN: {"status": "attempting"}}
    m._stamp_dispatch_skip_rearm_hint(states, HOT_TCIN)
    check("r3k_unit_attempting_untouched", states == {HOT_TCIN: {"status": "attempting"}}, states)
    m._save_states_unsafe = lambda s: (_ for _ in ()).throw(RuntimeError("disk"))
    m._stamp_dispatch_skip_rearm_hint({}, HOT_TCIN)
    check("r3k_unit_never_raises", True)


def test_r3_ac_latch_fsync_before_replace():
    """AC1-LATCH-FILE-NOT-DURABLE: the temp file is fsynced before the rename."""
    d = tempfile.mkdtemp(dir=_TMP)
    path = os.path.join(d, "latch.json")
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def spy_replace(a, b):
        events.append("replace")
        return real_replace(a, b)

    os.fsync, os.replace = spy_fsync, spy_replace
    try:
        ok, out = quiet(bpm_mod._ac_latch_save, path, {("W1/primary", HOT_TCIN): 9_000.0}, 10_000.0, 1800.0)
    finally:
        os.fsync, os.replace = real_fsync, real_replace
    check("r3l_fsync_then_replace", ok is True and events == ["fsync", "replace"], (ok, events, out))
    check("r3l_content_ok", bpm_mod._ac_latch_load(path, 10_000.0, 1800.0) == {("W1/primary", HOT_TCIN): 9_000.0})

    def bad_fsync(fd):
        raise OSError(5, "fsync not supported")

    os.fsync = bad_fsync
    try:
        ok, out = quiet(bpm_mod._ac_latch_save, path, {("W2/business", HOT_TCIN): 9_100.0}, 10_000.0, 1800.0)
    finally:
        os.fsync = real_fsync
    check("r3l_fsync_oserror_still_saves", ok is True
          and bpm_mod._ac_latch_load(path, 10_000.0, 1800.0) == {("W2/business", HOT_TCIN): 9_100.0}, (ok, out))


def test_r3_ac_latch_unreadable_is_loud():
    """AC1-LATCH-FILE-NOT-DURABLE: a latch file that exists but cannot be read
    is reported (console + error_log) instead of silently restoring nothing."""
    for label, content in (("garbled", b"{not json"), ("zeros", b"\x00" * 64), ("empty", b""),
                           ("not_utf8", b"\xff\xfe\x00{"), ("list", b"[1, 2]"),
                           ("latches_not_dict", b'{"latches": []}')):
        d = tempfile.mkdtemp(dir=_TMP)
        path = os.path.join(d, "latch.json")
        with open(path, "wb") as f:
            f.write(content)
        m = bare_mgr()
        with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST=None,
                 TARGET_AMBIGUOUS_COMMIT_LATCH_FILE=path):
            n, out = quiet(m._ac_restore_latches)
        try:
            with open(m._ac_error_log_path, encoding="utf-8") as f:
                elog = f.read()
        except FileNotFoundError:
            elog = ""
        check(f"r3l_unreadable_loud[{label}]", n == 0 and "[AMBIGUOUS_COMMIT] latch file unreadable" in out
              and "check order history" in out and "latch file unreadable" in elog, (n, out, elog))
        info = {}
        check(f"r3l_load_info[{label}]", bpm_mod._ac_latch_load(path, 10_000.0, 1800.0, info=info) == {}
              and info.get("unreadable"), info)
    for label, content in (("missing", None), ("no_latches", b'{"written_at": 1}'),
                           ("expired", b'{"latches": {"W1/primary|1010892069": 1.0}}')):
        d = tempfile.mkdtemp(dir=_TMP)
        path = os.path.join(d, "latch.json")
        if content is not None:
            with open(path, "wb") as f:
                f.write(content)
        m = bare_mgr()
        with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST=None,
                 TARGET_AMBIGUOUS_COMMIT_LATCH_FILE=path):
            n, out = quiet(m._ac_restore_latches)
        check(f"r3l_readable_silent[{label}]", n == 0 and "unreadable" not in out
              and not os.path.exists(m._ac_error_log_path), (n, out))


def main():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAIL:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

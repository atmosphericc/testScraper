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

This file is also the plan's home for the HS-1 / BG-1 / ID-1 enforcement tests;
that code is not built yet (stage S6 left nothing), so only the park is tested.

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
    region = src[i:i + 400]
    check("init_prints_banner", "_pb = _park_banner()" in region and "print(_pb)" in region, region)


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

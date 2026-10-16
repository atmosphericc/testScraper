#!/usr/bin/env python3
"""Offline test: S2-LIMKEY-PROBE (2026-10-15), the home half of EXP-LIMKEY.

TARGET_WARMUP_PROBE_TCINS (unset / empty / junk = OFF) names published, out-of-stock hot-class
TCINs; TARGET_WARMUP_PROBE_IDENTS names the account(s) allowed to probe. After a background
refill heartbeat returns, ONE extra evaluate on the same warmup tab fires the heartbeat's own
request at a probe TCIN. Its answer goes ONLY to a [LIMKEY_PROBE] line.

Drives the REAL executor code (real PurchaseExecutor, the real refill loop, the real
interceptor handler) with stub tabs and a stub session manager. No browser, no network, no bot.
The flag-off differential loads the PRE-CHANGE executor from git (2a327fec) and compares what a
stub heartbeat cycle evaluates and prints.

    python tests/test_limkey_probe_1015.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["TARGET_API_CAPTURE_CHECKOUT_STEPS"] = "false"   # no file writes in tests
os.environ["TARGET_API_CAPTURE_PLACE_ORDER"] = "false"

from zendriver import cdp  # noqa: E402
import src.session.purchase_executor as pe  # noqa: E402
from src.monitoring.stock_check_resilient import TcinStatus  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import _stock_status_snapshot  # noqa: E402

PRE_COMMIT = "2a327fecee3bab9266a57cc3e1e1151b4ba519d2"      # parent of the S2-LIMKEY-PROBE commit
PASSED, FAILED = [], []
FLAG = "TARGET_WARMUP_PROBE_TCINS"
A, B = "1010892076", "1011960739"
BOGUS = "81926151"
RESULT_RE = re.compile(r"\[LIMKEY_PROBE\] acct=(\S+) tcin=(\d+) status=(-?\d+) err_key=(\S+) hop=(\S+) "
                       r"line=(\S+) body_len=(-?\d+) n=(\d+)( err=\S+)?")
REAL_QUIET = pe.limkey_quiet_now
pe.limkey_quiet_now = lambda now: False        # the wall clock must not decide a test


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


class env:
    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeSM:
    def __init__(self, acct="primary", in_progress=False, proxy_url=None):
        self.browser = object()
        self.account_id = acct
        self.purchase_in_progress = in_progress
        self.proxy_url = proxy_url
        self.egress_age_exempt = False
        self.repairs = 0
        self.alerts = []

    def is_purchase_in_progress(self):
        return self.purchase_in_progress

    async def ensure_fresh_access_token(self, **kw):
        self.repairs += 1
        return True

    def _alert_critical(self, msg):
        self.alerts.append(msg)


class FakeTab:
    """Heartbeat (bogus TCIN) -> hb_status; any other cart_items POST is the probe: the stub
    'interceptor' notes (status, key, hop) into ex._limkey_wait like the real handler does."""

    def __init__(self, ex, hb_status=424, probe=None, probe_raises=None):
        self.url = "https://www.target.com/cart"
        self.ex = ex
        self.scripts = []
        self.hb_status = hb_status
        self.probe = probe or {"status": 429, "key": "ERR_A2C_TCIN_RATE_LIMITED", "hop": "-", "n": 0}
        self.probe_raises = probe_raises

    async def get(self, url):
        return None

    async def evaluate(self, script, await_promise=False):
        self.scripts.append(script)
        if "readyState" in script:
            return "complete"
        self.ex._cached_cart_headers_ts = time.time()      # the interceptor's header capture
        if f"tcin: '{BOGUS}'" in script:
            return self.hb_status
        if self.probe_raises is not None:
            raise self.probe_raises
        p = self.probe
        w = getattr(self.ex, "_limkey_wait", None)
        if w is not None:
            w.append((p["status"], p["key"], p["hop"]))
        if p["status"] == 0:
            return 0
        return json.dumps({"s": p["status"], "k": "", "n": p["n"], "b": ""})


def make_ex(cls=None, acct="primary", hb_status=424, probe=None, probe_raises=None, **sm_kw):
    cls = cls or pe.PurchaseExecutor
    sm = FakeSM(acct, **sm_kw)
    ex = cls(session_manager=sm)
    tab = FakeTab(ex, hb_status, probe, probe_raises)
    ex._warmup_browser_ref = sm.browser
    ex._warmup_tabs = [tab]
    ex._warmup_tab_cart_ts = {0: time.time() - 5.2}          # warm tab: no /cart nav
    return ex, tab, sm


def snap(kind, now):
    s = TcinStatus(tcin="x")
    if kind == "oos_never":
        s.last_checked_at, s.last_false_at = now - 1, now - 1
    elif kind == "live":
        s.in_stock, s.last_checked_at, s.last_true_at = True, now - 1, now - 1
    elif kind == "oos_5min":
        s.last_checked_at, s.last_false_at, s.last_true_at = now - 1, now - 1, now - 300
    elif kind == "oos_11min":
        s.last_checked_at, s.last_false_at, s.last_true_at = now - 1, now - 1, now - 660
    elif kind == "stale":
        s.last_checked_at, s.last_false_at = now - 90, now - 90
    return _stock_status_snapshot(s, now)


def stock_all(kind, now):
    return lambda t: snap(kind, now)


def seed_seen(tcins, ago=700.0, now=None):
    now = time.time() if now is None else now
    for t in tcins:
        pe._LIMKEY_STATE["first_seen"][t] = now - ago


def probes(tab):
    return [s for s in tab.scripts if "readyState" not in s and f"tcin: '{BOGUS}'" not in s]


async def tick(ex, now):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        await ex._limkey_probe_tick(0, now=now)
    return out.getvalue()


async def run_loop(ex, wait=0.5, mod=None):
    """One real _background_refill_loop iteration (heartbeat, then the probe tick), then cancel."""
    out = io.StringIO()
    spy = {"reached_sleep": 0}
    mod = mod or pe
    real_random = mod.random
    mod.random = SimpleNamespace(uniform=lambda a, b: spy.__setitem__("reached_sleep", spy["reached_sleep"] + 1) or 0.0,
                                **{k: getattr(real_random, k) for k in ("random", "choice", "randint")})
    try:
        with contextlib.redirect_stdout(out):
            t = asyncio.ensure_future(ex._background_refill_loop(0))
            await asyncio.sleep(wait)
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    finally:
        mod.random = real_random
    return out.getvalue(), spy["reached_sleep"]


def norm(text):
    return [re.sub(r"\d+(\.\d+)?s\b", "T", ln) for ln in text.splitlines()]


def load_pre_change_module():
    try:
        src = subprocess.run(["git", "show", f"{PRE_COMMIT}:src/session/purchase_executor.py"],
                             cwd=str(ROOT), capture_output=True, timeout=60, check=True).stdout.decode("utf-8")
    except Exception:
        return None
    mod = types.ModuleType("src.session.pe_pre_limkey")
    mod.__package__ = "src.session"
    mod.__file__ = str(ROOT / "src" / "session" / "pe_pre_limkey.py")
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


# ───────────────────────────── pure helpers ─────────────────────────────
def test_cfg_and_js():
    for label, val in [("unset", None), ("empty", ""), ("blank", "   "), ("junk", "abc,NaN"),
                       ("short", "123"), ("bogus_only", BOGUS), ("seps_only", ",;,")]:
        with env(**{FLAG: val}):
            check(f"cfg_off_{label}", pe.limkey_probe_cfg() is None)
    with env(**{FLAG: f"{A}, {B};{A} {BOGUS} zz", "TARGET_WARMUP_PROBE_IDENTS": "Primary ; alt-1",
                "TARGET_WARMUP_PROBE_MIN_GAP_S": None, "TARGET_WARMUP_PROBE_MAX_PER_HOUR": "500",
                "TARGET_WARMUP_PROBE_MAX_PER_RUN": "junk"}):
        c = pe.limkey_probe_cfg()
        check("cfg_tcins_deduped_and_filtered", c and c["tcins"] == [A, B], str(c))
        check("cfg_idents_lowercased", c and c["idents"] == {"primary", "alt-1"}, str(c))
        check("cfg_defaults_and_clamps", c and c["min_gap_s"] == 60.0 and c["per_hour"] == 60 and c["per_run"] == 240, str(c))
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_MIN_GAP_S": "1", "TARGET_WARMUP_PROBE_MAX_PER_HOUR": "0",
                "TARGET_WARMUP_PROBE_MAX_PER_RUN": "99999"}):
        c = pe.limkey_probe_cfg()
        check("cfg_floors", c and c["min_gap_s"] == 20.0 and c["per_hour"] == 1 and c["per_run"] == 1000, str(c))
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_MIN_GAP_S": "nan"}):
        check("cfg_nan_gap_is_default", pe.limkey_probe_cfg()["min_gap_s"] == 60.0)

    js =pe.limkey_probe_js(A)
    check("js_swaps_only_the_tcin_and_the_return",
          js.replace(pe._LIMKEY_RETURN_JS, "return resp.status;").replace(f"tcin: '{A}'", f"tcin: '{BOGUS}'")
          == pe._WARMUP_DUMMY_POST_JS)
    check("js_request_part_is_byte_identical_for_the_bogus_tcin",
          pe.limkey_probe_js(BOGUS).replace(pe._LIMKEY_RETURN_JS, "return resp.status;") == pe._WARMUP_DUMMY_POST_JS)
    for bad in ("1'; alert(1); '", "abc", "", "12345", "10108\n92076", "1010892076;1"):
        try:
            pe.limkey_probe_js(bad)
            check(f"js_rejects_{bad!r}", False, "no ValueError")
        except ValueError:
            check(f"js_rejects_{bad!r}", True)
    node = shutil.which("node")
    if node:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "probe.js"
            p.write_text("(" + js + ");\n", encoding="utf-8")
            r = subprocess.run([node, "--check", str(p)], capture_output=True, timeout=30)
            check("js_is_valid_javascript", r.returncode == 0, r.stderr.decode()[:200])
    else:
        print("[SKIP] js_is_valid_javascript (node not on PATH)")

    P = pe.limkey_parse_result
    check("parse_json_429_empty", P('{"s":429,"k":"","n":0,"b":""}') == {"status": 429, "key": "", "n": 0, "b": ""})
    check("parse_json_key", P(json.dumps({"s": 401, "k": "K", "n": 231, "b": "x"}))["key"] == "K")
    check("parse_bare_number", P(424)["status"] == 424 and P("0")["status"] == 0)
    check("parse_junk_is_minus_one", all(P(x)["status"] == -1 for x in (None, True, "{oops", "", [], {"s": "z"})))

    check("quiet_hours_real_fn",
          REAL_QUIET(time.mktime((2026, 10, 16, 3, 0, 0, 0, 0, -1))) is True
          and REAL_QUIET(time.mktime((2026, 10, 16, 0, 0, 0, 0, 0, -1))) is True
          and REAL_QUIET(time.mktime((2026, 10, 16, 5, 59, 59, 0, 0, -1))) is True
          and REAL_QUIET(time.mktime((2026, 10, 16, 6, 0, 0, 0, 0, -1))) is False
          and REAL_QUIET(time.mktime((2026, 10, 16, 23, 59, 0, 0, 0, -1))) is False
          and REAL_QUIET("junk") is True)


def test_reserve_limits():
    pe.limkey_probe_reset()
    cfg = {"min_gap_s": 20.0, "per_hour": 60, "per_run": 1000}
    t0 = 1_000_000.0
    granted = [pe.limkey_reserve(cfg, t0 + 20 * i) for i in range(60)]
    check("reserve_60_granted_in_the_hour", all(g == "" for g in granted), str(granted[:5]))
    check("reserve_gap_refused", pe.limkey_reserve(cfg, t0 + 20 * 59 + 5) == "gap")
    check("reserve_hour_cap_refused", pe.limkey_reserve(cfg, t0 + 20 * 60) == "hour_cap")
    check("reserve_hour_window_slides", pe.limkey_reserve(cfg, t0 + 3600 + 1) == "")
    pe.limkey_probe_reset()
    cfg = {"min_gap_s": 20.0, "per_hour": 60, "per_run": 3}
    r = [pe.limkey_reserve(cfg, t0 + 100 * i) for i in range(5)]
    check("reserve_run_cap", r == ["", "", "", "run_cap", "run_cap"], str(r))
    pe.limkey_probe_reset()
    pe._LIMKEY_STATE["stopped"] = True
    check("reserve_stopped", pe.limkey_reserve({"min_gap_s": 20.0, "per_hour": 60, "per_run": 9}, t0) == "stopped")
    pe.limkey_probe_reset()
    check("reserve_error_is_refusal", pe.limkey_reserve(None, t0) == "reserve_error")


# ───────────────────────────── flag off: byte-identical ─────────────────────────────
async def test_flag_off_differential():
    old = load_pre_change_module()
    if old is None:
        print("[SKIP] flag-off differential (git object not available)")
        check("flag_off_differential_skipped_no_git", True)
        return
    results = {}
    for label, cls, val in [("old", old.PurchaseExecutor, None), ("new_unset", pe.PurchaseExecutor, None),
                            ("new_junk", pe.PurchaseExecutor, "abc"), ("new_empty", pe.PurchaseExecutor, "")]:
        pe.limkey_probe_reset()
        with env(**{FLAG: val, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
            ex, tab, sm = make_ex(cls)
            out, sleeps = await run_loop(ex, mod=old if cls is old.PurchaseExecutor else None)
        results[label] = (list(tab.scripts), norm(out), sleeps, sm.repairs)
    base = results["old"]
    check("diff_old_ran_a_heartbeat", len(base[0]) == 1 and base[0][0] == old._WARMUP_DUMMY_POST_JS and base[2] == 1, str(base[0][:1]))
    for label in ("new_unset", "new_junk", "new_empty"):
        r = results[label]
        check(f"diff_{label}_evaluated_js_identical", r[0] == base[0])
        check(f"diff_{label}_printed_lines_identical", r[1] == base[1], f"\nold={base[1]}\nnew={r[1]}")
        check(f"diff_{label}_loop_reached_its_sleep", r[2] == 1)
    check("diff_dummy_constant_unchanged", pe._WARMUP_DUMMY_POST_JS == old._WARMUP_DUMMY_POST_JS)
    check("diff_no_limkey_line_when_off", not any("LIMKEY" in ln for ln in results["new_unset"][1]))


# ───────────────────────────── flag on ─────────────────────────────
async def test_probe_fires_after_heartbeat_and_stays_out_of_write_auth():
    for status, key, hop, n in [(401, "_ERR_AUTH_DENIED", "1", 231), (429, "ERR_A2C_TCIN_RATE_LIMITED", "-", 0),
                                (424, "ITEM_NOT_READY_FOR_LAUNCH,NOT_FOUND", "-", 90), (0, "-", "-", 0)]:
        pe.limkey_probe_reset()
        with env(**{FLAG: f"{A},{B}", "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
            ex, tab, sm = make_ex(probe={"status": status, "key": key, "hop": hop, "n": n})
            ex._stock_live_fn = stock_all("oos_never", time.time())
            seed_seen([A, B])
            called = {"confirm": 0}

            async def boom(*a, **k):
                called["confirm"] += 1
                return 401
            ex._confirm_write_auth_401 = boom
            out, sleeps = await run_loop(ex)
        m = RESULT_RE.findall(out)
        tag = f"s{status}"
        check(f"{tag}_one_result_line", len(m) == 1, out[-600:])
        if m:
            acct, tcin, st, ek, hp, ln, bl, nn, er = m[0]
            check(f"{tag}_fields", acct == "primary" and tcin in (A, B) and int(st) == status and ek == key
                  and hp == hop and ln == "home" and int(bl) == (n if status else -1) and int(nn) == 1 and not er,
                  str(m[0]))
        check(f"{tag}_two_evaluates_heartbeat_first", len(tab.scripts) == 2 and tab.scripts[0] == pe._WARMUP_DUMMY_POST_JS
              and f"tcin: '{BOGUS}'" not in tab.scripts[1] and "JSON.stringify({s: resp.status" in tab.scripts[1])
        check(f"{tag}_write_auth_untouched",
              called["confirm"] == 0 and sm.repairs == 0 and not sm.alerts and len(ex._bg_token_repair_times) == 0
              and ex._last_bg_token_repair_ts == 0.0 and "WRITE-AUTH DEAD" not in out
              and "token repair" not in out and "TOKEN CHURN" not in out)
        check(f"{tag}_no_landed_suspect", getattr(ex, "_dummy_landed_suspect_ts", 0.0) in (0.0, None))
        check(f"{tag}_heartbeat_line_reports_the_decoys_424_only",
              "dummy POST status 424 — write-auth alive" in out
              and (status == 424 or f"dummy POST status {status}" not in out), out[-500:])
        check(f"{tag}_loop_reached_its_sleep", sleeps == 1)
        check(f"{tag}_wait_list_cleared", getattr(ex, "_limkey_wait", "x") is None)


async def test_confirm_path_and_selftest_keep_the_bogus_tcin():
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        pe.limkey_probe_reset()
        ex, tab, sm = make_ex()
        tab.hb_status = 424
        with contextlib.redirect_stdout(io.StringIO()):
            res = await ex._confirm_write_auth_401(tab, 0, "primary")
        check("confirm_path_evaluates_only_the_constant",
              tab.scripts and all(s == pe._WARMUP_DUMMY_POST_JS for s in tab.scripts), str(len(tab.scripts)))
        check("confirm_path_result_is_the_heartbeat_status", res == 424, str(res))
    src = Path(pe.__file__).read_text(encoding="utf-8")
    check("probe_js_built_only_in_the_tick", src.count("limkey_probe_js(") == 2      # def + the one call
          and "js = limkey_probe_js(tcin)" in src)
    import ast
    tree = ast.parse(src)
    users = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, n) or ""
            if "_WARMUP_DUMMY_POST_JS" in seg and n.name != "limkey_probe_js":
                users[n.name] = seg
    check("dummy_const_users_unchanged", set(users) == {"_confirm_write_auth_401", "_refresh_on_tab", "_harvest_selftest"}, str(sorted(users)))
    check("selftest_and_confirm_have_no_probe_code",
          all("limkey" not in users[k].lower() for k in ("_confirm_write_auth_401", "_harvest_selftest", "_refresh_on_tab")))


async def test_guards():
    now = time.time()

    async def fires(label, mutate=None, kind="oos_never", sm_kw=None, env_extra=None, seen=True, expect=False,
                    t=None, **ex_attrs):
        pe.limkey_probe_reset()
        with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary", **(env_extra or {})}):
            ex, tab, sm = make_ex(**(sm_kw or {}))
            if kind is not None:
                ex._stock_live_fn = stock_all(kind, now)
            if seen:
                seed_seen([A], now=now)
            for k, v in ex_attrs.items():
                setattr(ex, k, v)
            if mutate:
                mutate(ex, tab, sm)
            out = await tick(ex, now if t is None else t)
        ok = (len(probes(tab)) == 1) == expect
        check(f"guard_{label}", ok, f"probes={len(probes(tab))} out={out.strip()[-200:]}")
        return out

    await fires("baseline_fires", expect=True)
    await fires("stock_live_now", kind="live")
    await fires("stock_in_stock_5min_ago", kind="oos_5min")
    await fires("stock_in_stock_11min_ago_ok", kind="oos_11min", expect=True)
    await fires("stock_read_stale", kind="stale")
    await fires("stock_view_missing", kind=None)
    await fires("stock_fn_raises", mutate=lambda ex, tab, sm: setattr(ex, "_stock_live_fn", lambda t: 1 / 0))
    await fires("stock_fn_returns_junk", mutate=lambda ex, tab, sm: setattr(ex, "_stock_live_fn", lambda t: "live"))
    out = await fires("watched_under_10min_since_first_seen", seen=False)
    check("guard_observing_says_so", "reason=observing" in out, out)
    await fires("purchase_in_progress", sm_kw={"in_progress": True})
    await fires("woncart_quiet", _woncart_active_until=now + 60)
    await fires("held_cart", _held_cart={"created": now})
    await fires("won_cart_ride", _won_cart_ride_until=now + 60)
    await fires("boot_selftest_armed", _harvest_selftest_armed=True)
    await fires("no_tab", mutate=lambda ex, tab, sm: setattr(ex, "_warmup_tabs", [None]))
    await fires("warmup_in_progress", _warmup_in_progress=True)
    await fires("sm_missing_method_fails_closed", mutate=lambda ex, tab, sm: setattr(sm, "is_purchase_in_progress", None))
    pe.limkey_quiet_now = REAL_QUIET
    try:
        quiet_t = time.mktime((2026, 10, 16, 3, 30, 0, 0, 0, -1))
        day_t = time.mktime((2026, 10, 16, 14, 30, 0, 0, 0, -1))
        pe.limkey_probe_reset()
        with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
            ex, tab, sm = make_ex()
            ex._stock_live_fn = stock_all("oos_never", quiet_t)
            seed_seen([A], now=quiet_t)
            out = await tick(ex, quiet_t)
            check("guard_quiet_hours_03_30", len(probes(tab)) == 0 and "reason=quiet_hours" in out, out)
            ex._stock_live_fn = stock_all("oos_never", day_t)
            seed_seen([A], now=day_t)
            await tick(ex, day_t)
            check("guard_daytime_fires", len(probes(tab)) == 1)
    finally:
        pe.limkey_quiet_now = lambda now: False

    # idents: only the listed account probes; empty idents = nobody
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        exs = {a: make_ex(acct=a) for a in ("primary", "alt-1", "business")}
        for a, (ex, tab, sm) in exs.items():
            ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        for i, a in enumerate(("business", "alt-1", "primary")):
            await tick(exs[a][0], now + i * 100)
        counts = {a: len(probes(exs[a][1])) for a in exs}
        check("idents_only_the_listed_probes", counts == {"primary": 1, "alt-1": 0, "business": 0}, str(counts))
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": None}):
        ex, tab, sm = make_ex()
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        out = await tick(ex, now)
        check("idents_empty_probes_nobody", len(probes(tab)) == 0 and "reason=ident_not_listed" in out, out)


async def test_fleet_gap_rotation_and_caps():
    now = time.time()
    pe.limkey_probe_reset()
    with env(**{FLAG: f"{A},{B}", "TARGET_WARMUP_PROBE_IDENTS": "primary,alt-1"}):
        e1, t1, _ = make_ex(acct="primary")
        e2, t2, _ = make_ex(acct="alt-1")
        for e in (e1, e2):
            e._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A, B], now=now)
        await tick(e1, now)
        await tick(e2, now + 1)
        await tick(e2, now + 30)
        await tick(e1, now + 59)
        check("gap_holds_across_two_executors", len(probes(t1)) + len(probes(t2)) == 1, f"{len(probes(t1))} {len(probes(t2))}")
        await tick(e2, now + 61)
        check("gap_reopens_after_min_gap", len(probes(t2)) == 1 and len(probes(t1)) == 1)
        got = [re.search(r"tcin: '(\d+)'", s).group(1) for s in probes(t1) + probes(t2)]
        check("probe_tcins_rotate", sorted(got) == sorted([A, B]), str(got))
    # per-run hard cap through the real tick
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary", "TARGET_WARMUP_PROBE_MAX_PER_RUN": "2"}):
        ex, tab, _ = make_ex()
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        for i in range(6):
            await tick(ex, now + 100 * i)
        check("per_run_cap_via_tick", len(probes(tab)) == 2, str(len(probes(tab))))
    # hourly cap through the real tick (gap 20 s -> the 61st in an hour is refused)
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary", "TARGET_WARMUP_PROBE_MIN_GAP_S": "20"}):
        ex, tab, _ = make_ex()
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        for i in range(70):
            await tick(ex, now + 20 * i)
        check("per_hour_cap_via_tick", len(probes(tab)) == 60, str(len(probes(tab))))


async def test_stock_view_resolution():
    now = time.time()
    pe.limkey_probe_reset()
    saved = pe._LIMKEY_STOCK_FN
    try:
        with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
            ex, tab, _ = make_ex()
            seed_seen([A], now=now)
            check("no_view_no_probe", (await tick(ex, now), len(probes(tab)))[1] == 0)
            pe.limkey_set_stock_fn(stock_all("oos_never", now))
            await tick(ex, now + 1)
            check("module_level_view_used", len(probes(tab)) == 1)
            pe.limkey_probe_reset()
            ex._stock_live_fn = stock_all("live", now)                  # the executor's own view wins
            await tick(ex, now + 500)
            check("executor_view_preferred", len(probes(tab)) == 1)
    finally:
        pe._LIMKEY_STOCK_FN = saved
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    i = app_src.find("limkey_set_stock_fn(global_purchase_manager.stock_snapshot)")
    check("app_wires_the_manager_stock_view_guarded",
          i > 0 and "try:" in app_src[max(0, i - 400):i] and "except Exception" in app_src[i:i + 500]
          and "TARGET_WARMUP_PROBE_TCINS" in app_src[max(0, i - 400):i])


async def test_probe_2xx_stops_the_run():
    now = time.time()
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex(probe={"status": 201, "key": "-", "hop": "-", "n": 900})
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        out = await tick(ex, now)
        check("2xx_result_line_printed", bool(RESULT_RE.search(out)) and "status=201" in out, out)
        check("2xx_loud_stop_line", "[LIMKEY_PROBE] event=STOP reason=probe_2xx" in out and "LINE WAS ADDED" in out, out)
        check("2xx_state_stopped", pe._LIMKEY_STATE["stopped"] is True)
        check("2xx_marks_the_cart_suspect_for_the_won_cart_loop", float(getattr(ex, "_dummy_landed_suspect_ts", 0.0) or 0.0) > 0.0)
        out2 = await tick(ex, now + 500)
        ex2, tab2, _ = make_ex(acct="primary")
        ex2._stock_live_fn = stock_all("oos_never", now)
        await tick(ex2, now + 900)
        check("2xx_no_more_probes_fleet_wide", len(probes(tab)) == 1 and len(probes(tab2)) == 0, out2)
        check("2xx_write_auth_untouched", sm.repairs == 0 and not sm.alerts and len(ex._bg_token_repair_times) == 0)
    pe.limkey_probe_reset()
    check("reset_clears_stop", pe._LIMKEY_STATE["stopped"] is False)


async def test_errors_never_reach_the_heartbeat():
    now = time.time()
    # the probe evaluate raises: logged with err=, the refill loop still reaches its sleep
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex(probe_raises=RuntimeError("socket closed"))
        ex._stock_live_fn = stock_all("oos_never", time.time())
        seed_seen([A])
        out, sleeps = await run_loop(ex)
        m = RESULT_RE.findall(out)
        check("evaluate_error_logged_not_raised", len(m) == 1 and m[0][2] == "-1" and m[0][8].strip() == "err=RuntimeError", out[-400:])
        check("evaluate_error_loop_continues", sleeps == 1 and ex._warmup_tabs[0] is tab)
        check("evaluate_error_no_repair", sm.repairs == 0 and "WRITE-AUTH DEAD" not in out)
    # the evaluate hangs: bounded by the 15 s wait_for (patched down), loop continues
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex()

        async def hang(script, await_promise=False):
            tab.scripts.append(script)
            if f"tcin: '{BOGUS}'" in script:
                ex._cached_cart_headers_ts = time.time()
                return 424
            await asyncio.sleep(30)
        tab.evaluate = hang
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        real_wait_for = pe.asyncio.wait_for
        pe.asyncio.wait_for = lambda aw, timeout=None: real_wait_for(aw, timeout=0.2)
        try:
            out = await tick(ex, now)
        finally:
            pe.asyncio.wait_for = real_wait_for
        m = RESULT_RE.findall(out)
        check("evaluate_timeout_logged", len(m) == 1 and m[0][8].strip() == "err=TimeoutError", out)
        check("evaluate_timeout_wait_list_cleared", ex._limkey_wait is None)
    # the JS builder blows up: event=error, no slot taken, no raise
    pe.limkey_probe_reset()
    real_js = pe.limkey_probe_js
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex()
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        pe.limkey_probe_js = lambda t: (_ for _ in ()).throw(ValueError("template drift"))
        try:
            out = await tick(ex, now)
        finally:
            pe.limkey_probe_js = real_js
        check("js_builder_error_logged_not_raised", "[LIMKEY_PROBE] event=error ValueError: template drift" in out, out)
        check("js_builder_error_takes_no_slot", pe._LIMKEY_STATE["total"] == 0 and len(probes(tab)) == 0)
    # a guard that raises = refused
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex()
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        ex._woncart_quiet = lambda: 1 / 0
        out = await tick(ex, now)
        check("guard_exception_means_no_probe", len(probes(tab)) == 0 and "reason=guard_error" in out, out)
    # constructing a real executor with the flag set (startup-path rule, 10-07)
    with env(**{FLAG: f"{A},{B}", "TARGET_WARMUP_PROBE_IDENTS": "primary", "TARGET_WARMUP_PROBE_MIN_GAP_S": "45"}):
        try:
            ex = pe.PurchaseExecutor(session_manager=FakeSM())
            check("real_executor_constructs_with_flag_set", ex is not None and pe.limkey_probe_cfg() is not None)
        except Exception as e:
            check("real_executor_constructs_with_flag_set", False, repr(e))
    # the loop's probe call is a no-op while the flag is off
    pe.limkey_probe_reset()
    with env(**{FLAG: None}):
        ex, tab, sm = make_ex()
        ex._limkey_probe_tick = None            # would raise if the loop called it
        out, sleeps = await run_loop(ex)
        check("loop_never_calls_the_tick_when_off", sleeps == 1 and "Refresh raised" not in out and "Unexpected error" not in out, out[-300:])


# ───────────────────────────── the interceptor stash ─────────────────────────────
class _CdpTab:
    def __init__(self, ex):
        self.ex = ex
        self.sent = []
        self.handlers = {}
        self.enabled_domains = []
        self.wait_len_at_continue = None

    async def send(self, gen):
        cmd = next(gen)
        self.sent.append((cmd.get("method", "?"), cmd.get("params", {})))
        if cmd.get("method") == "Fetch.continueRequest":
            w = getattr(self.ex, "_limkey_wait", None)
            self.wait_len_at_continue = None if w is None else len(w)
        try:
            gen.send({})
        except StopIteration:
            pass

    def add_handler(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def remove_handler(self, handler, event_type):
        self.handlers.get(event_type, []).remove(handler)


def _resp_event(req_id, status, key, hop=None):
    hdrs = [SimpleNamespace(name="tgt-cart-error-key", value=key)] if key else []
    if hop:
        hdrs.append(SimpleNamespace(name="x-ssx-hop", value=hop))
    return SimpleNamespace(
        request_id=cdp.fetch.RequestId(req_id), response_status_code=status, response_error_reason=None,
        response_headers=hdrs,
        request=SimpleNamespace(url="https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART",
                                method="POST", headers={"x-abc123": "t"}))


def _bare_cdp_ex():
    ex = object.__new__(pe.PurchaseExecutor)
    ex._cdp_continued_ids = set()
    ex._cdp_dedup_hits = 0
    ex._cached_cart_headers = {}
    ex._cached_cart_headers_ts = 0.0
    ex._shape_capture_ring = []
    ex._checkout_rejected = False
    ex.logger = logging.getLogger("limkey_test")
    return ex


async def test_interceptor_stash():
    ex = _bare_cdp_ex()
    tab = _CdpTab(ex)
    with contextlib.redirect_stdout(io.StringIO()):
        await ex._setup_cdp_fetch_interceptor(tab, persistent=True)
    h = tab.handlers[cdp.fetch.RequestPaused][-1]
    with contextlib.redirect_stdout(io.StringIO()):
        await h(_resp_event("r1", 429, "ERR_A2C_TCIN_RATE_LIMITED"))
    check("stash_off_by_default_no_attribute", not hasattr(ex, "_limkey_wait"))
    check("stash_off_response_still_continued", any(m == "Fetch.continueRequest" for m, _ in tab.sent))
    ex._limkey_wait = []
    with contextlib.redirect_stdout(io.StringIO()):
        await h(_resp_event("r2", 401, "_ERR_AUTH_DENIED", hop="1"))
    check("stash_on_warmup_records_status_key_hop", ex._limkey_wait == [(401, "_ERR_AUTH_DENIED", "1")], str(ex._limkey_wait))
    check("stash_written_before_the_continue", tab.wait_len_at_continue == 1, str(tab.wait_len_at_continue))
    with contextlib.redirect_stdout(io.StringIO()):
        await h(_resp_event("r3", 429, "ERR_A2C_TCIN_RATE_LIMITED"))
    check("stash_no_hop_header_is_dash", ex._limkey_wait[-1] == (429, "ERR_A2C_TCIN_RATE_LIMITED", "-"), str(ex._limkey_wait))
    ex2 = _bare_cdp_ex()
    tab2 = _CdpTab(ex2)
    with contextlib.redirect_stdout(io.StringIO()):
        await ex2._setup_cdp_fetch_interceptor(tab2, persistent=False)  # label 'main'
    h2 = tab2.handlers[cdp.fetch.RequestPaused][-1]
    ex2._limkey_wait = []
    with contextlib.redirect_stdout(io.StringIO()):
        await h2(_resp_event("r4", 429, "ERR_A2C_TCIN_RATE_LIMITED"))
    check("stash_ignores_the_main_tab", ex2._limkey_wait == [])
    with env(TARGET_ATC_RESPONSE_HEADER_CAPTURE="0"):
        ex3 = _bare_cdp_ex()
        tab3 = _CdpTab(ex3)
        with contextlib.redirect_stdout(io.StringIO()):
            await ex3._setup_cdp_fetch_interceptor(tab3, persistent=True)
        h3 = tab3.handlers[cdp.fetch.RequestPaused][-1]
        ex3._limkey_wait = []
        with contextlib.redirect_stdout(io.StringIO()):
            await h3(_resp_event("r5", 429, "ERR_A2C_TCIN_RATE_LIMITED"))
        check("stash_empty_when_capture_is_off_key_stays_unknown", ex3._limkey_wait == [])


async def test_err_key_unknown_is_question_mark():
    now = time.time()
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex(probe={"status": 429, "key": "-", "hop": "-", "n": 0})
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        orig = tab.evaluate

        async def no_interceptor(script, await_promise=False):
            ex._limkey_wait = ex._limkey_wait      # nothing appended: the interceptor saw nothing
            tab.scripts.append(script)
            return json.dumps({"s": 429, "k": "", "n": 0, "b": ""})
        tab.evaluate = no_interceptor
        out = await tick(ex, now)
        m = RESULT_RE.findall(out)
        check("unknown_key_is_question_mark", len(m) == 1 and m[0][3] == "?", out)
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex()
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)

        async def js_key(script, await_promise=False):
            tab.scripts.append(script)
            return json.dumps({"s": 429, "k": "ERR_A2C_TCIN_RATE_LIMITED", "n": 0, "b": ""})
        tab.evaluate = js_key
        out = await tick(ex, now)
        m = RESULT_RE.findall(out)
        check("page_visible_key_used_when_interceptor_silent", len(m) == 1 and m[0][3] == "ERR_A2C_TCIN_RATE_LIMITED", out)
    pe.limkey_probe_reset()
    with env(**{FLAG: A, "TARGET_WARMUP_PROBE_IDENTS": "primary"}):
        ex, tab, sm = make_ex(proxy_url="127.0.0.1:9999")
        ex._stock_live_fn = stock_all("oos_never", now)
        seed_seen([A], now=now)
        out = await tick(ex, now)
        check("line_field_proxy", "line=proxy" in out, out)
        pe.limkey_probe_reset()
        sm.egress_age_exempt = True
        seed_seen([A], now=now)
        out = await tick(ex, now)
        check("line_field_egress", "line=egress" in out, out)


async def amain():
    test_cfg_and_js()
    test_reserve_limits()
    await test_flag_off_differential()
    await test_probe_fires_after_heartbeat_and_stays_out_of_write_auth()
    await test_confirm_path_and_selftest_keep_the_bogus_tcin()
    await test_guards()
    await test_fleet_gap_rotation_and_caps()
    await test_stock_view_resolution()
    await test_probe_2xx_stops_the_run()
    await test_errors_never_reach_the_heartbeat()
    await test_interceptor_stash()
    await test_err_key_unknown_is_question_mark()


def main():
    asyncio.run(amain())
    print(f"\n===== test_limkey_probe_1015: {len(PASSED)} passed, {len(FAILED)} failed =====")
    if FAILED:
        print("FAILED:", ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""AC-1 (hot-sku 0916 plan P2): ambiguous-commit tag + per-(identity, TCIN) latch.

WHY: Target's checkout API has no idempotency key. Several paths can re-race a
TCIN after a place-order POST that MAY have committed server-side:
  * the fast-lane no-response guard returns checkout_navigation_failed, and the
    level re-arm (app.py) re-races every 'failed' TCIN;
  * the legacy no-response guard returns False and the page-text diagnosis can
    still call it retryable (stale 429 key from an earlier shot, busy copy);
  * purchase_impl_hang / the manager's execution_timeout can follow an
    in-flight POST.
TARGET_AMBIGUOUS_COMMIT_LATCH=1 tags those results and latches the identity off
the TCIN for TARGET_AMBIGUOUS_COMMIT_LATCH_S (1800). Flag 0 = exact prior dicts.

Offline: no browser, no network. Stubs only; the legacy diagnosis block is
executed from the REAL source text of _execute_purchase_impl in a temp cwd so
its logs/error_log.txt append never touches the repo.

Run: python tests/test_ambiguous_commit.py
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import io
import os
import re
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_FLAG = "TARGET_AMBIGUOUS_COMMIT_LATCH"
_TTL = "TARGET_AMBIGUOUS_COMMIT_LATCH_S"
for _k in (_FLAG, _TTL, "TARGET_API_CAPTURE_PLACE_ORDER", "TARGET_API_PLACE_ORDER_OBSERVE",
           "TARGET_DOM_FALLBACK_ON_NO_RESPONSE", "TARGET_CVV_DOM_FIRST"):
    os.environ.pop(_k, None)

import src.session.purchase_executor as pe_mod  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402

TCIN = "1010892069"
PASSED: list = []
FAILED: list = []
_TMP = tempfile.mkdtemp(prefix="ac1_")


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


def flag(on: bool):
    return env(**{_FLAG: "1" if on else None})


@contextlib.contextmanager
def in_tmp_cwd():
    old = os.getcwd()
    d = tempfile.mkdtemp(dir=_TMP)
    os.chdir(d)
    try:
        yield d
    finally:
        os.chdir(old)


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


# ───────────────────────────── executor stubs ──────────────────────────────

class _SM:
    account_id = "primary"

    def _load_account_cvv(self):
        return ""


def bare_pe():
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex.session_manager = _SM()
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._fastlane_placed = False
    ex._cvv_required = False
    ex._atc_referrer_pdp = False
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    ex._fast_selling_until = 0.0
    ex._won_cart_ride_until = 0.0
    ex._cached_cart_headers = {}
    ex._cached_cart_headers_ts = 0.0
    ex._shot_ttl_refresh_on = False
    ex._persist_cvv_challenge_flag = lambda: None
    ex._po_inflight = False
    ex._po_ambiguous = False
    ex._woncart_po_unresolved = False
    return ex


class _Tab:
    def __init__(self, raises=None, hang=False, result=None, url="https://www.target.com/checkout",
                 page_text=""):
        self.raises = raises
        self.hang = hang
        self.result = result if result is not None else {}
        self.url = url
        self.page_text = page_text
        self.gets = []

    async def evaluate(self, js, await_promise=False):
        if self.hang:
            await asyncio.sleep(30)
        if self.raises is not None:
            raise self.raises
        if "document.body" in js and "innerText" in js:
            return self.page_text
        return self.result

    async def get(self, url):
        self.gets.append(url)


LEGACY_KEYS = {"success", "tcin", "reason", "error", "execution_time"}


# ─────────────── 1. fast-lane no-response guard (PE _apply_fast_lane_result) ───────

def test_fast_lane_no_response_tag():
    fl = {"atc": {"status": 201}, "pre": {"status": 200},
          "po": {"status": 0, "fired": True, "body": ""}, "skip": "evaluate_timeout"}
    for on in (False, True):
        ex = bare_pe()
        with flag(on):
            (v, term), _ = quiet(ex._apply_fast_lane_result, fl, TCIN, time.time())
        check(f"fl_no_response_terminal[flag={int(on)}]", v == "terminal", v)
        check(f"fl_no_response_reason_unchanged[flag={int(on)}]",
              term["reason"] == "checkout_navigation_failed", term)
        if on:
            check("fl_no_response_tagged_flag1", term.get("ambiguous_commit") is True, term)
        else:
            check("fl_no_response_dict_identical_flag0", set(term) == LEGACY_KEYS, sorted(term))
        check(f"fl_no_response_sets_po_ambiguous[flag={int(on)}]", ex._po_ambiguous is True)


def test_fast_lane_received_rejection_not_tagged():
    fl = {"atc": {"status": 201}, "pre": {"status": 200},
          "po": {"status": 429, "fired": True, "body": ""}, "skip": ""}
    ex = bare_pe()
    ex._note_fast_selling_throttle = lambda: None
    with flag(True):
        (v, term), _ = quiet(ex._apply_fast_lane_result, fl, TCIN, time.time())
    check("fl_received_429_fallthrough", v == "fallthrough" and term is None, (v, term))
    check("fl_received_429_not_ambiguous", ex._po_ambiguous is False)


def test_fast_lane_evaluate_paths_clear_inflight():
    for label, tab in (("timeout", _Tab(raises=asyncio.TimeoutError())),
                       ("threw", _Tab(raises=RuntimeError("socket gone"))),
                       ("ok", _Tab(result={"atc": {"status": 401}, "pre": {}, "po": {},
                                           "skip": "atc_401"}))):
        ex = bare_pe()
        res, _ = quiet(asyncio.run, ex._api_fast_lane(tab, TCIN, 2, "{}"))
        check(f"fl_eval_{label}_inflight_cleared", ex._po_inflight is False)
        if label != "ok":
            check(f"fl_eval_{label}_reports_fired_status0",
                  res["po"]["fired"] is True and res["po"]["status"] == 0, res["po"])
            with flag(True):
                (v, term), _ = quiet(ex._apply_fast_lane_result, res, TCIN, time.time())
            check(f"fl_eval_{label}_terminal_tagged",
                  v == "terminal" and term.get("ambiguous_commit") is True, term)


# ──────────────── 2. execute_purchase hang branch (purchase_impl_hang) ──────────────

class _AsyncioShortTimeout:
    """Stand-in for the asyncio module inside purchase_executor whose
    timeout(140) is 0.3 s. Everything else is the real asyncio."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    def timeout(_delay):
        return asyncio.timeout(0.3)


def _run_hang(on: bool, during_po: bool):
    ex = bare_pe()
    ex._page_lock = asyncio.Lock()
    ex._note_atc_gate_outcome = lambda *a, **k: None

    async def fake_impl(tcin, quantity=1):
        ex._po_inflight = False
        ex._po_ambiguous = False
        if during_po:
            await ex._api_fast_lane(_Tab(hang=True), tcin, quantity, "{}")
        else:
            await asyncio.sleep(30)          # hang outside any place-order evaluate
        return {"success": True}

    ex._execute_purchase_impl = fake_impl
    real = pe_mod.asyncio
    pe_mod.asyncio = _AsyncioShortTimeout()
    try:
        with flag(on):
            res, _ = quiet(asyncio.run, ex.execute_purchase(TCIN, 2))
    finally:
        pe_mod.asyncio = real
    return ex, res


def test_hang_branch_tag():
    ex, res = _run_hang(on=False, during_po=True)
    check("hang_flag0_reason", res.get("reason") == "purchase_impl_hang", res)
    check("hang_flag0_dict_identical", set(res) == {"success", "tcin", "reason", "error"}, sorted(res))
    check("hang_flag0_error_keeps_websocket", "websocket" in res.get("error", ""), res)
    check("hang_cancel_leaves_inflight_true", ex._po_inflight is True)

    ex, res = _run_hang(on=True, during_po=True)
    check("hang_flag1_during_po_tagged", res.get("ambiguous_commit") is True, res)
    check("hang_flag1_reason_unchanged", res.get("reason") == "purchase_impl_hang", res)

    ex, res = _run_hang(on=True, during_po=False)
    check("hang_flag1_outside_po_not_ambiguous", res.get("ambiguous_commit") is False, res)


# ───────────────── 3. _api_place_order + legacy no-response guard ─────────────────

def test_api_place_order_inflight_cleared():
    ex = bare_pe()
    res, _ = quiet(asyncio.run, ex._api_place_order(_Tab(raises=RuntimeError("ws closed"))))
    check("apo_threw_status0", res["status"] == 0 and res["reason"].startswith("fetch_threw"), res)
    check("apo_threw_inflight_cleared", ex._po_inflight is False)
    ex = bare_pe()
    res, _ = quiet(asyncio.run, ex._api_place_order(_Tab(result={"status": 0, "body": "TypeError"})))
    check("apo_js_catch_status0", res["status"] == 0, res)
    check("apo_js_catch_inflight_cleared", ex._po_inflight is False)


def _place_order_with(results, reject_status=0):
    ex = bare_pe()
    seq = list(results)

    async def fake_apo(tab):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    async def no_hold(*a, **k):
        return False

    async def warm(*a, **k):
        return True

    ex._api_place_order = fake_apo
    ex._hold_cart_for_fast_selling = no_hold
    ex.warm_shape_headers = warm
    ex._note_fast_selling_throttle = lambda: None
    ex._checkout_reject_status = reject_status
    with env(TARGET_API_PLACE_ORDER="true", TARGET_CHECKOUT_INPLACE_DELAY_MIN="0",
             TARGET_CHECKOUT_INPLACE_DELAY_MAX="0", TARGET_WON_CART_RIDE="0"):
        ok, _ = quiet(asyncio.run, ex._place_order(_Tab()))
    return ex, ok


def test_legacy_guard_sets_ambiguous():
    ex, ok = _place_order_with([{"success": False, "status": 0, "reason": "fetch_threw:x", "body": ""}])
    check("legacy_guard_returns_false", ok is False, ok)
    check("legacy_guard_sets_po_ambiguous", ex._po_ambiguous is True)

    # stale 429 first shot, then a no-response re-shoot inside the in-place loop
    ex, ok = _place_order_with([
        {"success": False, "status": 429, "reason": "http_429", "body": ""},
        {"success": False, "status": 0, "reason": "fetch_threw:reset", "body": ""},
    ], reject_status=429)
    check("legacy_reshoot_no_response_false", ok is False, ok)
    check("legacy_reshoot_no_response_sets_ambiguous", ex._po_ambiguous is True)

    # a refusal that never fired must not latch
    ex, ok = _place_order_with([{"success": False, "status": 0, "reason": "capture_flag_active",
                                 "body": ""}])
    check("legacy_capture_refusal_not_ambiguous", ex._po_ambiguous is False)

    # a received rejection is not ambiguous
    ex, ok = _place_order_with([{"success": False, "status": 424, "reason": "reservation_failure",
                                 "body": ""}] * 6)
    check("legacy_received_424_not_ambiguous", ex._po_ambiguous is False)


# ─────────── 4. legacy diagnosis (real source block, executed in isolation) ──────────

def _diagnosis_fn():
    src = inspect.getsource(PurchaseExecutor._execute_purchase_impl)
    m = re.search(r"^( *)if not checkout_result:\n\s*# Diagnose: use interceptor reason", src, re.M)
    assert m, "diagnosis block anchor not found"
    end = src.index("return _co_fail", m.start()) + len("return _co_fail")
    block = textwrap.dedent(src[m.start():end])
    fn_src = ("async def _diag(self, tab, tcin, start_time, checkout_result):\n"
              + textwrap.indent(block, "    ")
              + "\n    return 'FELL_THROUGH'\n")
    ns = dict(vars(pe_mod))
    exec(compile(fn_src, "<diagnosis-block>", "exec"), ns)
    return ns["_diag"]


def _diag(on, *, ambiguous, rejected=False, status=0, key="", page=""):
    fn = _diagnosis_fn()
    ex = bare_pe()
    ex._po_ambiguous = ambiguous
    ex._checkout_rejected = rejected
    ex._checkout_reject_status = status
    ex._checkout_reject_reason = key

    async def clear(tab):
        return True

    ex._clear_cart = clear
    tab = _Tab(url="https://www.target.com/checkout", page_text=page)
    with flag(on), in_tmp_cwd():
        res, out = quiet(asyncio.run, fn(ex, tab, TCIN, time.time(), False))
    return res, out


def test_diagnosis_non_retryable_when_ambiguous():
    # busy copy on the page: today retryable
    r0, _ = _diag(False, ambiguous=True, page="we're busier than usual, please keep trying")
    check("diag_busy_flag0_retryable_today", r0["reason"] == "checkout_busy_retryable", r0)
    check("diag_busy_flag0_dict_identical",
          set(r0) == {"success", "tcin", "reason", "execution_time"}, sorted(r0))
    r1, out = _diag(True, ambiguous=True, page="we're busier than usual, please keep trying")
    check("diag_busy_flag1_non_retryable", r1["reason"] == "checkout_navigation_failed", r1)
    check("diag_busy_flag1_tagged", r1.get("ambiguous_commit") is True, r1)
    check("diag_busy_flag1_logs_override", "ambiguous commit" in out, out[-300:])

    # stale 429 key from an earlier shot of this purchase: today retryable
    r0, _ = _diag(False, ambiguous=True, rejected=True, status=429,
                  key="FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION")
    check("diag_stale429_flag0_retryable_today", r0["reason"] == "checkout_busy_retryable", r0)
    r1, _ = _diag(True, ambiguous=True, rejected=True, status=429,
                  key="FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION")
    check("diag_stale429_flag1_non_retryable", r1["reason"] == "checkout_navigation_failed", r1)
    check("diag_stale429_flag1_tagged", r1.get("ambiguous_commit") is True, r1)

    # not ambiguous: flag on changes nothing
    r1, _ = _diag(True, ambiguous=False, rejected=True, status=429, key="RATE_LIMIT")
    check("diag_not_ambiguous_flag1_still_retryable",
          r1["reason"] == "checkout_busy_retryable" and "ambiguous_commit" not in r1, r1)


def test_reset_per_purchase_pinned():
    src = inspect.getsource(PurchaseExecutor._execute_purchase_impl)
    head = src[:src.index("print(f\"[PURCHASE] Starting purchase for {tcin}\")")]
    for name in ("_po_inflight", "_po_ambiguous", "_woncart_po_unresolved"):
        check(f"reset_per_purchase[{name}]", f"self.{name} = False" in head)


# ─────────────────────────── 5. manager latch helpers ─────────────────────────────

def bare_mgr():
    m = object.__new__(BulletproofPurchaseManager)
    m._ac_latch = {}
    m._ac_latch_lock = threading.Lock()
    m._ac_skip_log_ts = {}
    m._ac_error_log_path = os.path.join(tempfile.mkdtemp(dir=_TMP), "error_log.txt")
    return m


def test_latch_mark_and_ttl():
    m = bare_mgr()
    with flag(True):
        _, out = quiet(m._ac_latch_mark, "W2/alt-1", TCIN, "checkout_navigation_failed")
        check("latch_log_line", f"[AMBIGUOUS_COMMIT] W2/alt-1 {TCIN} latched 1800s — check order history"
              in out, out)
        with open(m._ac_error_log_path, encoding="utf-8") as f:
            check("latch_error_log_line", "[AMBIGUOUS_COMMIT] W2/alt-1" in f.read())
        check("latch_skip_reason", m._thread_skip_reason("W2/alt-1", "alt-1", TCIN)
              == "ambiguous_commit_latched")
        check("latch_other_ident_free", m._thread_skip_reason("W1/primary", "primary", TCIN) == "")
        check("latch_other_tcin_free", m._thread_skip_reason("W2/alt-1", "alt-1", "111") == "")
        ts = m._ac_latch[("W2/alt-1", TCIN)]
        check("latch_left_before_ttl", m._ac_latched_left("W2/alt-1", TCIN, now=ts + 1799) > 0)
        check("latch_expired_after_ttl", m._ac_latched_left("W2/alt-1", TCIN, now=ts + 1801) == 0.0)
        check("latch_expired_entry_dropped", ("W2/alt-1", TCIN) not in m._ac_latch)
    with flag(False):
        m2 = bare_mgr()
        m2._ac_latch[("W2/alt-1", TCIN)] = time.time()
        check("flag0_latched_entry_ignored", m2._thread_skip_reason("W2/alt-1", "alt-1", TCIN) == "")
    with flag(True), env(**{_TTL: "120"}):
        m3 = bare_mgr()
        m3._ac_latch[("W1/primary", TCIN)] = 1000.0
        check("ttl_env_120_live", m3._ac_latched_left("W1/primary", TCIN, now=1119.0) > 0)
        check("ttl_env_120_expired", m3._ac_latched_left("W1/primary", TCIN, now=1121.0) == 0.0)
    for raw, want in (("abc", 1800.0), ("5", 60.0), ("1e9", 86400.0), ("nan", 1800.0),
                      (" 900 ", 900.0)):
        with env(**{_TTL: raw}):
            check(f"ttl_parse[{raw!r}]", bpm_mod._ac_latch_ttl_s() == want, bpm_mod._ac_latch_ttl_s())


def test_stub_without_latch_attrs():
    m = object.__new__(BulletproofPurchaseManager)
    m._ac_error_log_path = os.path.join(tempfile.mkdtemp(dir=_TMP), "e.txt")
    with flag(True):
        quiet(m._ac_latch_mark, "legacy", TCIN, "x")
        check("lazy_latch_state", m._thread_skip_reason("legacy", "", TCIN) == "ambiguous_commit_latched")


# ────────────── 6. manager thread: in-thread skip, latch on result/timeout ──────────

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
        return dict(r)

    async def warm_shape_headers(self, force_fresh=False):
        return True


class _Worker:
    def __init__(self, wid, acct, script, timeout=False):
        self.cfg = _Cfg(wid, acct)
        self.session_manager = _WSM()
        self.purchase_executor = _Exec(script)
        self.timeout = timeout

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

    def run_async(self, coro):
        f = concurrent.futures.Future()
        if self.timeout and getattr(coro, "__name__", "") == "execute_purchase":
            coro.close()

            def _raise(timeout=None):
                raise TimeoutError()
            f.result = _raise
            f.cancel = lambda: True
            return f
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
                  TARGET_401_PULSE="0", TARGET_RACE_ALL_WORKERS="1", TARGET_RETRY_WHILE_IN_STOCK="1")


def _race(m, max_qty=1):
    m._start_real_purchase(TCIN, "Tin", {}, max_qty=max_qty)
    ok = m.done.wait(20)
    time.sleep(0.2)
    return ok


def test_thread_skip_attempt1_records_reason():
    w1 = _Worker(1, "primary", [{"success": False, "tcin": TCIN, "reason": "rate_limited_429"}])
    w2 = _Worker(2, "alt-1", [{"success": False, "tcin": TCIN, "reason": "rate_limited_429"}])
    m = race_mgr([w1, w2])
    with env(**FAST_RETRY, TARGET_RETRY_WHILE_IN_STOCK_MAX="1"), flag(True):
        m._ac_latch[("W2/alt-1", TCIN)] = time.time()
        (ok, out) = quiet(_race, m)
    check("skip_race_finished", ok, m.recorded)
    rec = dict(m.recorded)
    check("skip_attempt1_reason_recorded",
          rec.get("W2/alt-1", {}).get("reason") == "ambiguous_commit_latched", rec)
    check("skip_attempt1_nothing_fired", w2.purchase_executor.calls == 0, w2.purchase_executor.calls)
    check("skip_other_identity_fired", w1.purchase_executor.calls == 1, w1.purchase_executor.calls)
    check("skip_log_line", "W2/alt-1 sits out" in out, out[-400:])
    br = m._states.get(TCIN, {}).get("race_breakdown", {})
    check("skip_breakdown_has_reason", br.get("W2/alt-1") == "ambiguous_commit_latched", br)


def test_ambiguous_result_latches_and_attempt2_keeps_result():
    amb = {"success": False, "tcin": TCIN, "reason": "rate_limited_429", "ambiguous_commit": True}
    w1 = _Worker(1, "primary", [amb])
    w2 = _Worker(2, "alt-1", [{"success": False, "tcin": TCIN, "reason": "button_x"}])
    m = race_mgr([w1, w2])
    with env(**FAST_RETRY, TARGET_RETRY_WHILE_IN_STOCK_MAX="5"), flag(True):
        (ok, out) = quiet(_race, m)
    check("amb_race_finished", ok, m.recorded)
    check("amb_latched_primary", ("W1/primary", TCIN) in m._ac_latch, m._ac_latch)
    check("amb_not_latched_alt1", ("W2/alt-1", TCIN) not in m._ac_latch, m._ac_latch)
    check("amb_attempt2_not_fired", w1.purchase_executor.calls == 1, w1.purchase_executor.calls)
    rec = dict(m.recorded)
    check("amb_attempt2_keeps_real_result",
          rec.get("W1/primary", {}).get("reason") == "rate_limited_429"
          and rec["W1/primary"].get("ambiguous_commit") is True, rec)
    check("amb_log_stop_line", "stops re-racing" in out, out[-400:])

    # flag off: same result neither latches nor stops the transient retry loop
    w1 = _Worker(1, "primary", [amb])
    w2 = _Worker(2, "alt-1", [{"success": False, "tcin": TCIN, "reason": "button_x"}])
    m = race_mgr([w1, w2])
    with env(**FAST_RETRY, TARGET_RETRY_WHILE_IN_STOCK_MAX="3"), flag(False):
        (ok, out) = quiet(_race, m)
    check("amb_flag0_no_latch", not m._ac_latch, m._ac_latch)
    check("amb_flag0_retries_as_today", w1.purchase_executor.calls == 3, w1.purchase_executor.calls)
    check("amb_flag0_no_new_log", "[AMBIGUOUS_COMMIT]" not in out and "sits out" not in out)


def test_execution_timeout_latches():
    for on in (True, False):
        w1 = _Worker(1, "primary", [{"success": False}], timeout=True)
        w2 = _Worker(2, "alt-1", [{"success": False, "tcin": TCIN, "reason": "button_x"}])
        m = race_mgr([w1, w2])
        with env(**FAST_RETRY), flag(on):
            (ok, out) = quiet(_race, m)
        rec = dict(m.recorded)
        check(f"timeout_reason[flag={int(on)}]",
              rec.get("W1/primary", {}).get("reason") == "execution_timeout", rec)
        if on:
            check("timeout_latched_flag1", ("W1/primary", TCIN) in m._ac_latch, m._ac_latch)
            check("timeout_log_flag1", "why=execution_timeout" in out, out[-400:])
        else:
            check("timeout_not_latched_flag0", not m._ac_latch, m._ac_latch)


# ───────────────────── 7. pre-dispatch: all latched → no race ─────────────────────

def stock_mgr(workers):
    m = race_mgr(workers)
    m._warmup_cycle_counter = 5            # not a warm cycle
    m._maybe_run_session_sentinel = lambda: None
    m.started = []

    def _start(tcin, title, max_qty=1):
        m.started.append(tcin)
        return {"success": True, "duration": 1}
    m.start_purchase = _start
    m._states = {TCIN: {"status": "ready"}}
    return m


def test_pre_dispatch_all_latched():
    stock = {TCIN: {"in_stock": True, "title": "Tin", "max_qty": 1}}
    mk = lambda: [_Worker(1, "primary", [{}]), _Worker(2, "alt-1", [{}])]  # noqa: E731

    m = stock_mgr(mk())
    with flag(True):
        now = time.time()
        m._ac_latch[("W1/primary", TCIN)] = now
        m._ac_latch[("W2/alt-1", TCIN)] = now
        _, out = quiet(m.process_stock_data, stock)
        _, out2 = quiet(m.process_stock_data, stock)
    check("predispatch_all_latched_no_start", m.started == [], m.started)
    check("predispatch_logged_once_per_minute",
          out.count("every candidate identity is latched") == 1
          and "every candidate identity is latched" not in out2, (out[-300:], out2[-300:]))

    m = stock_mgr(mk())
    with flag(True):
        m._ac_latch[("W1/primary", TCIN)] = time.time()
        quiet(m.process_stock_data, stock)
    check("predispatch_one_free_starts", m.started == [TCIN], m.started)

    m = stock_mgr(mk())
    with flag(False):
        now = time.time()
        m._ac_latch[("W1/primary", TCIN)] = now
        m._ac_latch[("W2/alt-1", TCIN)] = now
        quiet(m.process_stock_data, stock)
    check("predispatch_flag0_starts", m.started == [TCIN], m.started)

    m = stock_mgr(mk())
    with flag(True):
        m._ac_latch[("W1/primary", TCIN)] = time.time() - 1801
        m._ac_latch[("W2/alt-1", TCIN)] = time.time() - 1801
        quiet(m.process_stock_data, stock)
    check("predispatch_expired_starts", m.started == [TCIN], m.started)


def test_r3_legacy_5xx_unresolved():
    """R3 review (WC-5XX-NOT-AMBIGUOUS): with AC-1 armed, a received 408/5xx on
    the legacy API place-order (first shot or an in-place re-shoot) is treated
    like no response: no DOM click, _po_ambiguous set. AC-1 off or the
    kill-switch = the prior behaviour."""
    for st in (500, 502, 503, 504, 408):
        with flag(True):
            ex, ok = _place_order_with([{"success": False, "status": st, "reason": f"http_{st}", "body": ""}])
        check(f"r3_legacy_5xx_ambiguous[{st}]", ok is False and ex._po_ambiguous is True, (ok, ex._po_ambiguous))
    with flag(True):
        ex, ok = _place_order_with([
            {"success": False, "status": 429, "reason": "http_429", "body": ""},
            {"success": False, "status": 502, "reason": "http_502", "body": ""},
        ], reject_status=429)
    check("r3_legacy_reshoot_5xx_ambiguous", ok is False and ex._po_ambiguous is True)
    with flag(False):
        ex, ok = _place_order_with([{"success": False, "status": 504, "reason": "http_504", "body": ""}])
    check("r3_legacy_5xx_flag_off_unchanged", ex._po_ambiguous is False)
    with flag(True), env(TARGET_PO_5XX_AMBIGUOUS="0"):
        ex, ok = _place_order_with([{"success": False, "status": 504, "reason": "http_504", "body": ""}])
    check("r3_legacy_5xx_killswitch", ex._po_ambiguous is False)
    with flag(True):
        ex, ok = _place_order_with([{"success": False, "status": 409, "reason": "http_409", "body": ""}])
    check("r3_legacy_409_not_ambiguous", ex._po_ambiguous is False)


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

#!/usr/bin/env python3
"""Offline tests for the 2026-10-06 mass-restock dispatch fixes (docs/CLAIMS.md
C-1006-01..06). Every flag defaults OFF = the exact prior code.

FX-1006-FAIR  TARGET_MULTI_SKU_FAIR_SHARE=1 -- a TCIN dispatched while other
              raceable TCINs read in stock takes ceil(free / (1 + others)).
FX-1006-MISS  TARGET_MULTI_SKU_MISS_REARM=1 -- a [MULTI_SKU_MISS] TCIN keeps a
              rearm_hint_ts so the level re-arm re-offers it; multi-TCIN events
              go least-recently-dispatched first; single-row writes.
FX-1006-KEEP  TARGET_STOCK_RESET_KEEP_LIVE_FAILED=1 -- a 'failed' row absent
              from an event is not reset while the monitor reads it in stock.
FX-1006-YIELD TARGET_WAVE_FIRST_YIELD_MISSED=1 -- a racer in the 55-70 s
              wave-first wait frees its account for a skipped live TCIN.
FX-1006-LOCK  TARGET_SHARED_LOCK_ORDER_FIX=1 -- app.py takes the purchase-state
              snapshot before shared_data.lock (source pin only here).
FAIR-FRESH-1015 TARGET_FAIR_FRESH_FIX=1 -- an already-raced wave is never a FAIR
              contender, a flickered newest flip stays "fresh" (pre-open OOS
              stamp), and the FAIR defer print has its own log kind (the 10-09
              window-9 replay, section 9).

Stub workers / stub checker only: no browser, no network.
Run: python tests/test_mass_restock_1006.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402  (env, quiet, check, race_mgr, _Worker)
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402
from src.purchasing.state_store import StateStore  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet

A, B, C, D = "1010000001", "1010000002", "1010000003", "1010000004"
O_, F_ = "1010000005", "1010000006"
FLAGS = dict(TARGET_MULTI_SKU_FAIR_SHARE=None, TARGET_MULTI_SKU_MISS_REARM=None,
             TARGET_STOCK_RESET_KEEP_LIVE_FAILED=None, TARGET_WAVE_FIRST_YIELD_MISSED=None,
             TARGET_STOCK_PROBE_FRESH_S=None, TARGET_STOCK_HYST_S=None,
             TARGET_MULTI_SKU_FAIR_FRESH_S=None, TARGET_WAVE_FIRST_YIELD_MAX_AGE_S=None,
             TARGET_MULTI_SKU_MISS_REARM_PURCHASED_HOLD_S=None,
             TARGET_AMBIGUOUS_COMMIT_LATCH=None, TARGET_PARK_ACCOUNT_TCINS=None,
             TARGET_HOME_SHARE_GUARD=None, TARGET_IDENTITY_REST=None,
             TARGET_REARM_AFTER_EMERGENCY_RESET=None, TARGET_QTY_PER_TCIN=None,
             TARGET_RACE_STATE_STARTED_AT_GUARD=None, TARGET_WARMUP_CYCLE_SKIP_ON_STOCK=None,
             TARGET_FAIR_FRESH_FIX=None)
MSKU = dict(TARGET_MULTI_SKU_DISPATCH="1", TARGET_MULTI_SKU_MAX_CONCURRENT="3",
            TARGET_MULTI_SKU_WORKERS_PER_TCIN="3", TARGET_MULTI_SKU_CAP_ALWAYS="1",
            TARGET_MULTI_SKU_RESERVE_TTL_S="120", TARGET_STUCK_RESET_LIVE_GUARD="1")


def _st(in_stock=True, win_ago=0.5, age=0.2, lf_ago=None):
    """A TcinStatus stand-in, relative to now: latest read `age` s ago; an
    in-stock TCIN's last out-of-stock read `lf_ago` s ago -- by default the read
    just before its window opened (a genuine out->in flip)."""
    now = time.time()
    if lf_ago is None:
        lf_ago = win_ago + 0.34
    lc = now - age
    return SimpleNamespace(in_stock=in_stock, last_checked_at=lc,
                           last_true_at=lc if in_stock else now - 300.0,
                           last_false_at=now - lf_ago if in_stock else lc,
                           window_start_at=now - win_ago if in_stock else now - 400.0,
                           title="x", availability_status="IN_STOCK" if in_stock else "OOS")


def _with_checker(m, statuses):
    m.stock_monitor = SimpleNamespace(_resilient_checker=SimpleNamespace(_tcin_status=statuses))
    return m


class _PW:
    def __init__(self, wid, acct):
        self.cfg = SimpleNamespace(worker_id=wid, account_id=acct)
        self.purchase_executor = SimpleNamespace(_held_cart=None)

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"


class _Pool:
    def __init__(self, ws):
        self._w = list(ws)

    def ready_workers(self):
        return list(self._w)

    @property
    def primary(self):
        return self._w[0]

    @property
    def workers(self):
        return list(self._w)


def _fleet():
    return [_PW(1, "primary"), _PW(2, "business"), _PW(3, "alt-1")]


def _mgr(states=None, statuses=None, store=False):
    """A manager without __init__ (as tests/test_retry_oos_stop.py builds one)."""
    m = H.bare_mgr()
    m._state_lock = threading.RLock()
    if store:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        m.state_store = StateStore(path)
        for k, v in (states or {}).items():
            m.state_store.set(k, v)
    else:
        m._states = {k: dict(v) for k, v in (states or {}).items()}
        m._load_states_unsafe = lambda: {k: dict(v) for k, v in m._states.items()}

        def _save(s):
            m._states = {k: dict(v) for k, v in s.items()}
            return True
        m._save_states_unsafe = _save
    m.status_callback = None
    m._active_purchases = {}
    m.worker_pool = _Pool(_fleet())
    m._warmup_cycle_counter = 5
    m._maybe_run_session_sentinel = lambda: None
    m.started = []

    def _start(tcin, title, max_qty=1):
        m.started.append(tcin)
        return {"success": True, "duration": 1}
    m.start_purchase = _start
    _with_checker(m, statuses or {})
    return m


def _rows(m):
    return m.state_store.get_all() if getattr(m, "state_store", None) is not None else m._states


def _race_on(m, tcin, n=3, age_s=5.0):
    """A live race on `tcin`: 'attempting' row, live racer threads, every worker reserved."""
    stop = threading.Event()
    th = threading.Thread(target=stop.wait, daemon=True)
    th.start()
    now = time.time()
    row = {"status": "attempting", "started_at": now - age_s, "real_purchase": True, "race": True}
    if getattr(m, "state_store", None) is not None:
        m.state_store.set(tcin, row)
    else:
        m._states[tcin] = row
    ws = m.worker_pool.ready_workers()[:n]
    m._active_purchases = {f"{tcin}#W{w.cfg.worker_id}": {"thread": th, "started_at": now - age_s,
                                                         "status": "running", "worker": w.label()}
                           for w in ws}
    m._worker_reservations = {w.label(): (tcin, now - age_s) for w in ws}
    return stop


# ── 0. flags ─────────────────────────────────────────────────────────────────

def test_flags_default_off():
    with env(**FLAGS):
        check("fair_off", bpm_mod._fair_share_on() is False)
        check("miss_off", bpm_mod._miss_rearm_on() is False)
        check("keep_off", bpm_mod._keep_live_failed_on() is False)
        check("yield_off", bpm_mod._wave_yield_on() is False)
    with env(**{**FLAGS, "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        check("yield_inert_without_miss", bpm_mod._wave_yield_on() is False)
    with env(**{**FLAGS, "TARGET_WAVE_FIRST_YIELD_MISSED": "1", "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        check("yield_on_with_miss", bpm_mod._wave_yield_on() is True)
    with env(**{**FLAGS, "TARGET_MULTI_SKU_FAIR_SHARE": "0", "TARGET_MULTI_SKU_MISS_REARM": "true"}):
        check("only_exact_1_arms", not bpm_mod._fair_share_on() and not bpm_mod._miss_rearm_on())


def test_reads_in_stock_pure():
    now = time.time()
    f = bpm_mod._fx1006_reads_in_stock
    with env(**FLAGS):
        check("ris_fresh_true", f({"age": 0.3, "last_true": now - 0.3, "last_false": now - 9}, now))
        check("ris_latest_false", not f({"age": 0.3, "last_true": now - 5, "last_false": now - 0.3}, now))
        check("ris_stale", not f({"age": 16.0, "last_true": now - 16, "last_false": 0.0}, now))
        check("ris_no_age", not f({"age": None, "last_true": now, "last_false": 0.0}, now))
        check("ris_never_true", not f({"age": 0.3, "last_true": 0.0, "last_false": 0.0}, now))
        check("ris_garbage_never_raises", f(None, now) is False and f({"age": "x"}, now) is False)


# ── 1. FX-1006-FAIR ──────────────────────────────────────────────────────────

def _fair(m, tcin, limit=3, others=()):
    return m._fair_share_plan(tcin, m.worker_pool.ready_workers(), limit, set(others))[0]


def test_fair_share_limits():
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1"}):
        m = _mgr(statuses={A: _st(), B: _st(), C: _st()})
        n, out = quiet(_fair, m, A)
        check("fair_three_live_takes_1", n == 1, (n, out))
        check("fair_logs_line", "[MULTI_SKU_FAIR_SHARE]" in out and "2 other fresh" in out, out)
        m = _mgr(statuses={A: _st(), B: _st()})
        check("fair_two_live_takes_2", _fair(m, A) == 2)
        m = _mgr(statuses={A: _st()})
        n, out = quiet(_fair, m, A)
        check("fair_lone_tcin_full_cap", n == 3 and "[MULTI_SKU_FAIR_SHARE]" not in out, out)
        m = _mgr(statuses={A: _st(), B: _st(win_ago=300.0)})
        check("fair_old_window_bare_ready_not_contender", _fair(m, A) == 3)
        # verifier 10-06: a TCIN failing at the limiter for minutes must not shrink
        # a fresh flip's volley, hinted or not.
        m = _mgr(states={B: {"status": "failed"}}, statuses={A: _st(), B: _st(win_ago=300.0)})
        check("fair_old_window_failed_not_contender", _fair(m, A) == 3)
        m = _mgr(states={B: {"status": "ready", "rearm_hint_ts": time.time() - 5}},
                 statuses={A: _st(), B: _st(win_ago=300.0)})
        check("fair_old_window_hinted_not_contender", _fair(m, A) == 3)
        # verifier #4: a TCIN already raced ('failed', even on a young window) has spent
        # its first shots -- it must not hold an account back from a fresh flip.
        m = _mgr(states={B: {"status": "failed"}}, statuses={A: _st(), B: _st(win_ago=15.0)})
        check("fair_young_window_failed_not_contender", _fair(m, A) == 3)
        m = _mgr(states={B: {"status": "interrupted"}}, statuses={A: _st(), B: _st(win_ago=1.0)})
        check("fair_interrupted_not_contender", _fair(m, A) == 3)
        m = _mgr(statuses={A: _st(), B: _st(win_ago=25.0)})
        check("fair_window_past_young_s_not_contender", _fair(m, A) == 3)
        # verifier #4: a flicker back inside the 20 s hysteresis keeps its OLD window --
        # it must not shrink a fresh flip (it is still MISS-hinted and re-offered).
        m = _mgr(statuses={A: _st(), B: _st(win_ago=300.0, lf_ago=3.0)})
        check("fair_flicker_back_not_contender", _fair(m, A) == 3)
        for st in ("purchased", "attempting", "queued"):
            m = _mgr(states={B: {"status": st}}, statuses={A: _st(), B: _st()})
            check(f"fair_{st}_not_contender", _fair(m, A) == 3)
        m = _mgr(statuses={A: _st(), B: _st(in_stock=False)})
        check("fair_oos_not_contender", _fair(m, A) == 3)
        m = _mgr(statuses={A: _st(), B: _st(age=20.0)})
        check("fair_stale_read_not_contender", _fair(m, A) == 3)
        m = _mgr(statuses={A: _st(), B: _st()})
        m._fx1006_note_purchase(B)
        check("fair_bought_this_window_not_contender", _fair(m, A) == 3)
        # bought in an EARLIER window (a real out-then-in flip since): a repeat buy
        # on a new window is the existing rule, so it counts again.
        m = _mgr(statuses={A: _st(), B: _st(win_ago=0.5)})
        m._fx1006_purchase_ts = {B: time.time() - 120.0}
        check("fair_bought_earlier_window_is_contender", _fair(m, A) == 2)
        m = _mgr(statuses={A: _st(), D: _st(win_ago=300.0)})
        m.worker_pool._w[0].purchase_executor._held_cart = {"tcin": D}
        with env(TARGET_HELD_CART_REENTRY="1"):
            w1, w2 = m.worker_pool._w[0], m.worker_pool._w[1]
            check("sits_out_held_live_other", m._fx1006_worker_sits_out(w1, A) is True)
            check("sits_out_not_for_its_own_held_tcin", m._fx1006_worker_sits_out(w1, D) is False)
            check("sits_out_clean_worker_false", m._fx1006_worker_sits_out(w2, A) is False)
        with env(TARGET_HELD_CART_REENTRY=None):
            check("sits_out_held_ignored_without_reentry", m._fx1006_worker_sits_out(w1, A) is False)
        m = _mgr(statuses={A: _st(), B: _st()})
        check("fair_reserved_other_not_contender", _fair(m, A, others={B}) == 3)
        m = _mgr(statuses={A: _st(), B: _st(), C: _st()})
        check("fair_limit_1_unchanged", _fair(m, A, limit=1) == 1)
        m = _mgr(statuses={A: _st(), B: _st()})
        m.stock_monitor = None
        check("fair_no_checker_unchanged", _fair(m, A) == 3)


class _BlockExec:
    """A stub executor whose add-to-cart blocks until `gate` opens, so a race stays
    live while the next stock event is processed (the same-sweep callbacks)."""
    def __init__(self, gate, result=None):
        self.gate = gate
        self.result = dict(result or EDGE)
        self.calls = []
        self._won_cart_ride_until = 0.0
        self._held_cart = None

    async def execute_purchase(self, tcin, quantity=1):
        self.calls.append(tcin)
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        return dict(self.result, tcin=tcin)

    async def warm_shape_headers(self, force_fresh=False):
        return True


E2E_ENV = dict(TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0", TARGET_RETRY_WARM="0",
               TARGET_WAVE_FIRST_ONLY="0", TARGET_SHOT_BANK_GATE="0", TARGET_401_PULSE="0",
               TARGET_RACE_ALL_WORKERS="1", TARGET_RETRY_WHILE_IN_STOCK="0",
               TARGET_ATC_DCO_BURST="0", TARGET_RETRY_STOP_WHEN_OOS="0")


def _e2e_mgr(statuses, gate, gates=None):
    """The REAL dispatch: process_stock_data -> start_purchase's reservation and
    racer threads (_start_real_purchase; start_purchase itself only adds the
    purchase-log tee) against stub workers. `gates`: one gate per worker."""
    ws = [H._Worker(1, "primary", [EDGE]), H._Worker(2, "business", [EDGE]), H._Worker(3, "alt-1", [EDGE])]
    for i, w in enumerate(ws):
        w.purchase_executor = _BlockExec(gates[i] if gates else gate)
    m = H.race_mgr(ws)
    m._warmup_cycle_counter = 5
    m._maybe_run_session_sentinel = lambda: None
    m.use_real_purchasing = True
    m.start_purchase = lambda tcin, title, max_qty=1: BulletproofPurchaseManager._start_real_purchase(
        m, tcin, title, m._load_states_unsafe(), max_qty=max_qty)
    _with_checker(m, statuses)
    return m, ws


def _flip_events(m, tcins):
    """One single-TCIN event per flipped TCIN, in response order (C-0924-02)."""
    out = ""
    for t in tcins:
        ev = {t: {"in_stock": True, "title": t, "max_qty": 2}}
        _, o1 = quiet(m.reset_completed_purchases_by_stock_status, ev)   # app.py step 1
        _, o2 = quiet(m.process_stock_data, ev)                           # step 2
        out += o1 + o2
    return out


def _settle(m, gate, ws):
    gate.set()
    deadline = time.time() + 15
    while time.time() < deadline and any(i["thread"].is_alive() for i in list(m._active_purchases.values())):
        time.sleep(0.05)
    time.sleep(0.2)


def _shots(ws):
    return {w.label(): list(w.purchase_executor.calls) for w in ws}


def test_fair_share_e2e_same_sweep():
    for flag in ("1", None):
        with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": flag}):
            gate = threading.Event()
            m, ws = _e2e_mgr({A: _st(), B: _st(), C: _st()}, gate)
            try:
                out = _flip_events(m, [A, B, C])
                time.sleep(0.3)
                shots = _shots(ws)
                res = dict(m._worker_reservations)
            finally:
                _settle(m, gate, ws)
            fired = sorted(t for v in shots.values() for t in v)
            if flag:
                check("e2e_fair_each_tcin_one_first_shot", fired == sorted([A, B, C]), (shots, out[-1500:]))
                check("e2e_fair_each_account_one_tcin", all(len(v) == 1 for v in shots.values()), shots)
                check("e2e_fair_three_reservations", sorted(t for t, _ in res.values()) == sorted([A, B, C]), res)
                check("e2e_fair_logged", out.count("[MULTI_SKU_FAIR_SHARE]") >= 2, out[-1500:])
                check("e2e_fair_no_miss", "[MULTI_SKU_MISS]" not in out, out[-1500:])
            else:
                check("e2e_off_first_tcin_takes_all", fired == [A, A, A], shots)
                check("e2e_off_others_missed", out.count("[MULTI_SKU_MISS]") == 2, out[-1500:])
            check(f"e2e_{'fair' if flag else 'off'}_reservations_released", m._worker_reservations == {},
                  m._worker_reservations)
    # two live: the first takes ceil(3/2)=2, the second the remaining 1
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st(), B: _st()}, gate)
        try:
            _flip_events(m, [A, B])
            time.sleep(0.3)
            fired = sorted(t for v in _shots(ws).values() for t in v)
        finally:
            _settle(m, gate, ws)
        check("e2e_fair_two_live_2_and_1", fired == sorted([A, A, B]), fired)
    # a lone live TCIN with others on OLD windows still races every account
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st(), B: _st(win_ago=600.0), C: _st(win_ago=900.0)}, gate)
        m._states[B] = {"status": "failed", "completed_at": time.time() - 500}
        try:
            _flip_events(m, [A])
            time.sleep(0.3)
            fired = sorted(t for v in _shots(ws).values() for t in v)
        finally:
            _settle(m, gate, ws)
        check("e2e_fair_old_windows_do_not_shrink_fresh_flip", fired == [A, A, A], fired)


def test_fair_share_e2e_sit_out_order_and_miss():
    """W1 holds a live won cart for D (it would sit out anything else): it is
    never reserved. A and B (both fresh) share the two firing accounts 1/1."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_HELD_CART_REENTRY": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st(), B: _st(), D: _st(win_ago=600.0)}, gate)
        ws[0].purchase_executor._held_cart = {"tcin": D, "created": time.time() - 30}
        m._states[B] = {"status": "ready"}
        try:
            out = _flip_events(m, [A, B])
            time.sleep(0.3)
            shots = _shots(ws)
            b_row = dict(m._states.get(B) or {})
        finally:
            _settle(m, gate, ws)
        check("sitout_W1_never_reserved", shots["W1/primary"] == [], (shots, out[-1500:]))
        check("sitout_A_and_B_one_firing_account_each",
              sorted(shots["W2/business"] + shots["W3/alt-1"]) == sorted([A, B]), (shots, out[-1500:]))
        check("sitout_logged_sitter", "1 would sit it out" in out, out[-1500:])
        check("sitout_B_row_dispatched_not_hinted", b_row.get("status") == "attempting", b_row)


def test_fair_share_source_pin():
    src = (ROOT / "src/purchasing/bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    i = src.index("_limit = _ms['per_tcin'] if (_others or _ms.get('cap_always')) else len(ready_workers)")
    j = src.index("dispatch_workers = self._reserve_workers_for_tcin(tcin, _reserve_order, _limit)", i)
    seg = src[i:j]
    check("pin_fair_between_limit_and_reserve",
          "if _fair_share_on():" in seg and "_limit, _reserve_order = self._fair_share_plan(tcin, ready_workers, _limit, _others)" in seg, seg)
    check("pin_reserve_order_defaults_to_ready_workers", "_reserve_order = ready_workers\n" in seg, seg)


# ── 2. FX-1006-MISS ──────────────────────────────────────────────────────────

def _miss_mgr(store=False, b_status=None):
    m = _mgr(states={B: {"status": "ready"}}, statuses={A: _st(), B: _st(**(b_status or {}))}, store=store)
    stop = _race_on(m, A)
    return m, stop


EV_B = {B: {"in_stock": True, "title": "B", "max_qty": 2}}


def test_miss_flag_off_bare_ready():
    with env(**{**FLAGS, **MSKU}):
        m, stop = _miss_mgr()
        try:
            _, out = quiet(m.process_stock_data, EV_B)
        finally:
            stop.set()
        check("off_miss_printed", "[MULTI_SKU_MISS]" in out, out[-600:])
        check("off_no_dispatch", m.started == [], m.started)
        check("off_no_hint", "rearm_hint_ts" not in _rows(m)[B], _rows(m)[B])


def test_miss_flag_on_hints_and_restamps():
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m, stop = _miss_mgr()
        try:
            _, out = quiet(m.process_stock_data, EV_B)
            h1 = _rows(m)[B].get("rearm_hint_ts")
            time.sleep(0.05)
            _, out2 = quiet(m.process_stock_data, EV_B)
            h2 = _rows(m)[B].get("rearm_hint_ts")
        finally:
            stop.set()
        check("on_miss_printed", "[MULTI_SKU_MISS]" in out, out[-600:])
        check("on_hint_stamped", bool(h1), _rows(m)[B])
        check("on_restamped_each_miss", bool(h2) and h2 > h1, (h1, h2))
        check("on_log_rate_limited", out.count("[MULTI_SKU_MISS_REARM]") == 1
              and "[MULTI_SKU_MISS_REARM]" not in out2, (out[-300:], out2[-300:]))
        check("on_pending_registered", B in m._fx1006_miss_pending, getattr(m, "_fx1006_miss_pending", None))
        check("on_race_row_untouched", _rows(m)[A]["status"] == "attempting", _rows(m)[A])


def test_miss_no_stamp_cases():
    base = {**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}
    with env(**base):
        for name, kw in (("oos_latest", {"in_stock": False}), ("read_past_rearm_tolerance", {"age": 100.0})):
            m, stop = _miss_mgr(b_status=kw)
            try:
                quiet(m.process_stock_data, EV_B)
            finally:
                stop.set()
            check(f"no_stamp_{name}", "rearm_hint_ts" not in _rows(m)[B], _rows(m)[B])
        m, stop = _miss_mgr()
        m._fx1006_note_purchase(B)
        try:
            quiet(m.process_stock_data, EV_B)
        finally:
            stop.set()
        check("no_stamp_bought_this_window", "rearm_hint_ts" not in _rows(m)[B], _rows(m)[B])
        m, stop = _miss_mgr()
        m._fx1006_purchase_ts = {B: time.time() - 120.0}     # bought before this window opened
        try:
            quiet(m.process_stock_data, EV_B)
        finally:
            stop.set()
        check("stamp_bought_in_earlier_window", bool(_rows(m)[B].get("rearm_hint_ts")), _rows(m)[B])


def test_miss_hint_feeds_level_rearm_and_free_worker_dispatches():
    from app import StockMonitorThread
    build = StockMonitorThread._build_level_rearm_map
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m, stop = _miss_mgr()
        try:
            quiet(m.process_stock_data, EV_B)
            proj = m.get_all_states()
            rearm = build({B: m.stock_monitor._resilient_checker._tcin_status[B]}, proj, time.time())
            check("rearm_map_offers_hinted_B", set(rearm) == {B}, rearm)
            # one racer on A finishes: its thread is gone and its reservation released
            done = threading.Thread(target=lambda: None)
            done.start()
            done.join()
            m._active_purchases[f"{A}#W3"]["thread"] = done
            m._worker_reservations.pop("W3/alt-1", None)
            _, out = quiet(m.process_stock_data, rearm)
        finally:
            stop.set()
        check("freed_worker_takes_B", m.started == [B], (m.started, out[-600:]))
        check("dispatch_clears_pending", B not in m._fx1006_miss_pending, m._fx1006_miss_pending)
        check("dispatch_recorded", B in m._fx1006_last_dispatch, getattr(m, "_fx1006_last_dispatch", None))
    with env(**{**FLAGS, **MSKU}):
        m, stop = _miss_mgr()
        try:
            quiet(m.process_stock_data, EV_B)
            rearm = build({B: m.stock_monitor._resilient_checker._tcin_status[B]},
                          m.get_all_states(), time.time())
        finally:
            stop.set()
        check("flag_off_rearm_map_empty_C0924_01", rearm == {}, rearm)


def test_miss_rearm_e2e_freed_worker_takes_B():
    """REAL reservations: A takes all 3 (fair share off), B flips while they race
    -> MISS + hint; one account's race ends -> the level re-arm map offers B and
    the freed account fires B's first shot."""
    from app import StockMonitorThread
    build = StockMonitorThread._build_level_rearm_map
    for flag in ("1", None):
        with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_MISS_REARM": flag}):
            gates = [threading.Event(), threading.Event(), threading.Event()]
            m, ws = _e2e_mgr({A: _st(), B: _st(win_ago=2.0)}, None, gates=gates)
            m._states[B] = {"status": "ready"}
            try:
                _flip_events(m, [A])
                time.sleep(0.2)
                _flip_events(m, [B])                    # B flips while A holds the fleet
                gates[2].set()                          # alt-1's race on A ends
                deadline = time.time() + 5
                while time.time() < deadline and "W3/alt-1" in m._worker_reservations:
                    time.sleep(0.05)
                time.sleep(0.2)
                rearm = build(m.stock_monitor._resilient_checker._tcin_status, m.get_all_states(), time.time())
                if rearm:
                    _, out = quiet(m.process_stock_data, rearm)
                time.sleep(0.3)
                shots = _shots(ws)
            finally:
                for g in gates:
                    g.set()
                _settle(m, gates[0], ws)
            tag = "on" if flag else "off"
            if flag:
                check("e2e_miss_rearm_offers_B", B in rearm, rearm)
                check("e2e_miss_freed_alt1_fires_B", shots["W3/alt-1"] == [A, B]
                      and shots["W1/primary"] == [A] and shots["W2/business"] == [A], shots)
            else:
                check("e2e_off_B_never_offered_C0924_01", B not in rearm, rearm)
                check("e2e_off_B_never_fired", B not in str(shots), shots)
            check(f"e2e_{tag}_reservations_released", m._worker_reservations == {}, m._worker_reservations)


def test_single_row_write_no_clobber():
    """D6: a hint save must not revert a row written after the loop's snapshot."""
    for flag, expect_attempting in ((None, False), ("1", True)):
        with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": flag}):
            m = _mgr(states={A: {"status": "ready"}, B: {"status": "ready"}},
                     statuses={A: _st(), B: _st()}, store=True)
            snap = m._load_states_unsafe()
            m.state_store.set(A, {"status": "attempting", "started_at": time.time()})  # start_purchase's write
            with m._state_lock:
                m._stamp_dispatch_skip_rearm_hint(snap, B)          # the AC-1 path, same helper shape
            rows = m.state_store.get_all()
            tag = "on" if flag else "off"
            check(f"clobber_{tag}_A_status", (rows[A]["status"] == "attempting") is expect_attempting, rows)
            check(f"clobber_{tag}_B_hinted", bool(rows[B].get("rearm_hint_ts")), rows)
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m = _mgr(states={A: {"status": "ready"}, B: {"status": "ready"}},
                 statuses={A: _st(), B: _st()}, store=True)
        snap = m._load_states_unsafe()
        m.state_store.set(A, {"status": "attempting", "started_at": time.time()})
        with m._state_lock:
            m._stamp_miss_rearm_hint(snap, B)
        rows = m.state_store.get_all()
        check("miss_stamp_keeps_A_attempting", rows[A]["status"] == "attempting", rows)
        check("miss_stamp_B_hinted", bool(rows[B].get("rearm_hint_ts")), rows)


def test_emergency_reset_single_row():
    """A multi-TCIN event: X dispatched first, then Y ('failed' inside its error delay)
    hits the emergency reset, whose full-snapshot save reverted X's 'attempting' row."""
    X, Y = A, B
    ev = {X: {"in_stock": True, "title": "X"}, Y: {"in_stock": True, "title": "Y"}}
    for flag, expect in ((None, "ready"), ("1", "attempting")):
        with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": flag}):
            m = _mgr(states={X: {"status": "ready"}, Y: {"status": "failed", "completed_at": time.time()}},
                     statuses={X: _st(), Y: _st()}, store=True)

            def _start(tcin, title, max_qty=1, m=m):
                m.started.append(tcin)
                m.state_store.set(tcin, {"status": "attempting", "started_at": time.time()})
                return {"success": True, "duration": 1}
            m.start_purchase = _start
            _, out = quiet(m.process_stock_data, ev)
            rows = m.state_store.get_all()
            tag = "on" if flag else "off"
            check(f"emerg_{tag}_reset_ran", "[PURCHASE_EMERGENCY]" in out, out[-500:])
            check(f"emerg_{tag}_X_{expect}", rows[X]["status"] == expect, rows)


def test_lru_order_for_multi_tcin_events():
    ev = {A: {"in_stock": True, "title": "A"}, B: {"in_stock": True, "title": "B"}}
    with env(**{**FLAGS, **MSKU}):
        m = _mgr(states={A: {"status": "ready"}, B: {"status": "ready"}}, statuses={A: _st(), B: _st()})
        m._fx1006_last_dispatch = {A: time.time() - 10}
        quiet(m.process_stock_data, ev)
        check("lru_off_config_order", m.started == [A, B], m.started)
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m = _mgr(states={A: {"status": "ready"}, B: {"status": "ready"}}, statuses={A: _st(), B: _st()})
        m._fx1006_last_dispatch = {A: time.time() - 10}
        quiet(m.process_stock_data, ev)
        check("lru_on_never_dispatched_first", m.started == [B, A], m.started)


# ── 3. FX-1006-KEEP ──────────────────────────────────────────────────────────

def _reset(m, ev):
    return m.reset_completed_purchases_by_stock_status(ev)


def test_keep_live_failed():
    ev_c = {C: {"in_stock": True}}
    old = time.time() - 60
    cases = (
        # name, A row, A status read, flag, expected A status after
        ("off_failed_live_reset", {"status": "failed", "completed_at": old}, _st(win_ago=120), None, "ready"),
        ("on_failed_live_kept", {"status": "failed", "completed_at": old}, _st(win_ago=120), "1", "failed"),
        ("on_failed_oos_reset", {"status": "failed", "completed_at": old}, _st(in_stock=False), "1", "ready"),
        ("on_failed_stale_reset", {"status": "failed", "completed_at": old}, _st(win_ago=120, age=20.0), "1", "ready"),
        ("on_purchased_live_unchanged_rule", {"status": "purchased", "completed_at": old}, _st(win_ago=120), "1", "ready"),
        # verifier #5: a failure left from an EARLIER window resets like HEAD
        ("on_failed_earlier_window_reset", {"status": "failed", "completed_at": old}, _st(win_ago=10), "1", "ready"),
        ("on_failed_no_completed_at_reset", {"status": "failed"}, _st(win_ago=120), "1", "ready"),
    )
    for name, row, st, flag, expect in cases:
        with env(**{**FLAGS, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": flag}):
            m = _mgr(states={A: row, C: {"status": "ready"}}, statuses={A: st, C: _st()})
            quiet(_reset, m, ev_c)
            check(f"keep_{name}", _rows(m)[A]["status"] == expect, _rows(m)[A])
    with env(**{**FLAGS, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": "1"}):
        m = _mgr(states={A: {"status": "failed", "completed_at": old}}, statuses={A: _st(win_ago=120)})
        quiet(_reset, m, {A: {"in_stock": False}})
        check("keep_on_explicit_oos_in_event_reset", _rows(m)[A]["status"] == "ready", _rows(m)[A])


def test_keep_defensive_reset():
    """The 30 s 'stuck completed' reset: flag off -> bare 'ready' + ERROR lines (HEAD);
    flag on -> a live, unbought 'failed' row is left alone (no ERROR); bought this
    window or out of stock -> reset exactly as HEAD."""
    ev = {C: {"in_stock": False}}
    cases = ((None, {}, "ready", True), ("1", {}, "failed", False),
             ("1", {"bought": True}, "ready", True), ("1", {"oos": True}, "ready", True),
             ("1", {"earlier": True}, "ready", True))
    for flag, opt, want, want_err in cases:
        with env(**{**FLAGS, **MSKU, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": flag}):
            m = _mgr(states={A: {"status": "failed", "completed_at": time.time() - 60}},
                     statuses={A: _st(in_stock=not opt.get("oos"), win_ago=10 if opt.get("earlier") else 300),
                               C: _st(in_stock=False)})
            if opt.get("bought"):
                m._fx1006_note_purchase(A)
            _, out = quiet(m.process_stock_data, ev)
            row = _rows(m)[A]
            tag = f"{'on' if flag else 'off'}_{'_'.join(opt) or 'live'}"
            check(f"defensive_{tag}_status_{want}", row["status"] == want, (row, out[-400:]))
            check(f"defensive_{tag}_error_{want_err}", ("[PURCHASE_PROCESS_ERROR]" in out) is want_err, out[-400:])
            check(f"defensive_{tag}_no_hint", not row.get("rearm_hint_ts"), row)


# ── 4. FX-1006-YIELD ─────────────────────────────────────────────────────────

def _yield_mgr(pending_ago=1.0, b_row=None, b_status=None):
    m = _mgr(states={B: b_row or {"status": "ready"}}, statuses={A: _st(), B: _st(**(b_status or {}))})
    m._fx1006_miss_pending = {B: {"first_miss_ts": time.time() - pending_ago,
                                  "window_start": time.time() - pending_ago}}
    return m


def test_yield_target_rules():
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        m = _yield_mgr()
        check("yt_names_B", m._wave_yield_target(A, "W1/primary") == B)
        check("yt_single_pending_second_racer_also_yields", m._wave_yield_target(A, "W2/business") == B)
        check("yt_same_racer_again_ok", m._wave_yield_target(A, "W1/primary") == B)
        # two pending: the second racer goes to the one with fewer recent claims
        m = _mgr(states={B: {"status": "ready"}, C: {"status": "ready"}},
                 statuses={A: _st(), B: _st(win_ago=3.0), C: _st(win_ago=1.0)})
        now = time.time()
        m._fx1006_miss_pending = {B: {"first_miss_ts": now, "window_start": now - 3.0},
                                  C: {"first_miss_ts": now, "window_start": now - 1.0}}
        r1 = m._wave_yield_target(A, "W1/primary")
        r2 = m._wave_yield_target(A, "W2/business")
        check("yt_two_pending_oldest_first", r1 == B, r1)
        check("yt_two_pending_spread", r2 == C, r2)
        m = _yield_mgr()
        check("yt_own_tcin_none", m._wave_yield_target(B, "W1/primary") is None)
        m = _yield_mgr()
        m._worker_reservations = {"W9/x": (B, time.time())}
        check("yt_reserved_none", m._wave_yield_target(A, "W1/primary") is None)
        m = _yield_mgr(b_status={"win_ago": 30.0})
        check("yt_window_too_old_none", m._wave_yield_target(A, "W1/primary") is None)
        m = _yield_mgr(b_row={"status": "attempting"})
        check("yt_not_ready_none", m._wave_yield_target(A, "W1/primary") is None)
        m = _yield_mgr(b_status={"in_stock": False})
        check("yt_oos_none", m._wave_yield_target(A, "W1/primary") is None)
        m = _yield_mgr()
        m._fx1006_note_purchase(B)
        check("yt_bought_none", m._wave_yield_target(A, "W1/primary") is None)
        m = _mgr()
        check("yt_no_pending_none", m._wave_yield_target(A, "W1/primary") is None)
        # the yielding account would only sit B out (a live held cart for C)
        m = _yield_mgr()
        _with_checker(m, {A: _st(), B: _st(), C: _st(win_ago=300.0)})
        m.worker_pool._w[0].purchase_executor._held_cart = {"tcin": C}
        with env(TARGET_HELD_CART_REENTRY="1"):
            check("yt_sit_out_account_not_freed", m._wave_yield_target(A, "W1/primary") is None)
            check("yt_other_account_still_freed", m._wave_yield_target(A, "W2/business") == B)


def test_yield_sleep():
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        m = _yield_mgr()
        t0 = time.time()
        r = m._wave_yield_sleep(A, "W1/primary", 30.0)
        check("ys_immediate", r == B and time.time() - t0 < 0.3, (r, time.time() - t0))
        m = _mgr(statuses={A: _st()})
        t0 = time.time()
        r = m._wave_yield_sleep(A, "W1/primary", 0.7)
        el = time.time() - t0
        check("ys_full_wait_none", r is None and 0.65 <= el < 1.3, (r, el))
        m = _mgr(states={B: {"status": "ready"}}, statuses={A: _st(), B: _st()})

        def _late():
            time.sleep(0.4)
            with bpm_mod._FX1006_LOCK:
                m._fx1006_dict("_fx1006_miss_pending")[B] = {"first_miss_ts": time.time(),
                                                            "window_start": time.time() - 0.4}
        threading.Thread(target=_late, daemon=True).start()
        t0 = time.time()
        r = m._wave_yield_sleep(A, "W1/primary", 10.0)
        el = time.time() - t0
        check("ys_picks_up_mid_wait", r == B and el < 1.5, (r, el))


EDGE = {"success": False, "reason": "rate_limited_429", "gate_kind": "edge",
        "error": "ATC rate-limited (429)"}
RACE_ENV = dict(TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0",
                TARGET_RETRY_WARM="0", TARGET_WAVE_FIRST_ONLY="1", TARGET_WAVE_FIRST_EDGE="1",
                TARGET_SHOT_BANK_GATE="0", TARGET_401_PULSE="0", TARGET_RACE_ALL_WORKERS="1",
                TARGET_RETRY_WHILE_IN_STOCK="1", TARGET_RETRY_WHILE_IN_STOCK_MAX="3",
                TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="110", TARGET_ATC_DCO_AS_EDGE_CADENCE="1",
                TARGET_ATC_DCO_BURST="0", TARGET_RETRY_STOP_WHEN_OOS="0",
                TARGET_MULTI_SKU_DISPATCH=None)


def test_yield_in_real_race_loop():
    """The REAL race loop: 3 accounts draw an edge 429 on A, three skipped TCINs are
    pending -> each racer leaves its 55-70 s wait at once, keeping its result."""
    T1, T2, T3 = B, C, D
    ws = [H._Worker(1, "primary", [EDGE]), H._Worker(2, "business", [EDGE]), H._Worker(3, "alt-1", [EDGE])]
    m = H.race_mgr(ws)
    for t in (T1, T2, T3):
        m._states[t] = {"status": "ready"}
    _with_checker(m, {A: _st(), T1: _st(), T2: _st(), T3: _st()})
    now = time.time()
    m._fx1006_miss_pending = {t: {"first_miss_ts": now, "window_start": now - 1.0} for t in (T1, T2, T3)}
    with env(**{**FLAGS, **RACE_ENV, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1",
                "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        t0 = time.time()
        ok, out = quiet(H._race, m, A)
        el = time.time() - t0
    check("race_reserved_then_released", "[MULTI_SKU_DISPATCH]" in out and m._worker_reservations == {},
          (m._worker_reservations, out[-600:]))
    check("race_finished_fast", ok and el < 12.0, (ok, el, out[-800:]))
    check("race_three_yields", out.count("[WAVE_YIELD]") == 3, out[-1500:])
    check("race_each_fired_once", [w.purchase_executor.calls for w in ws] == [1, 1, 1],
          [w.purchase_executor.calls for w in ws])
    check("race_results_kept_edge", len(m.recorded) == 3
          and all(r.get("reason") == "rate_limited_429" for _, r in m.recorded), m.recorded)
    named = set(re.findall(r"skipped live TCIN (\d+)", out))
    check("race_yields_spread_over_pending", named == {T1, T2, T3}, named)


def test_yield_never_on_checkout_level_result():
    """A checkout-level failure (no gate_kind) takes the ordinary cadence, never
    the wave-first wait, so it can never yield -- even with skipped TCINs pending."""
    busy = {"success": False, "reason": "checkout_busy_retryable", "error": "busy"}
    ws = [H._Worker(1, "primary", [busy]), H._Worker(2, "business", [busy]), H._Worker(3, "alt-1", [busy])]
    m = H.race_mgr(ws)
    m._states[B] = {"status": "ready"}
    _with_checker(m, {A: _st(), B: _st()})
    now = time.time()
    m._fx1006_miss_pending = {B: {"first_miss_ts": now, "window_start": now - 1.0}}
    with env(**{**FLAGS, **RACE_ENV, "TARGET_MULTI_SKU_MISS_REARM": "1",
                "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        ok, out = quiet(H._race, m, A)
    check("checkout_level_race_finished", ok, out[-600:])
    check("checkout_level_no_yield", "[WAVE_YIELD]" not in out, out[-1200:])


def test_fair_share_e2e_assignment_spends_the_sitter_first():
    """Verifier #4 C3: alt-1 is parked on B (it sits B out, not A). Two fresh flips:
    A must take alt-1 plus one more, leaving an account B can use -- not strand B."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_PARK_ACCOUNT_TCINS": f"alt-1:{B}"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st(), B: _st()}, gate)
        m._states[B] = {"status": "ready"}
        try:
            out = _flip_events(m, [A, B])
            time.sleep(0.3)
            shots = _shots(ws)
        finally:
            _settle(m, gate, ws)
        check("assign_alt1_spent_on_A", shots["W3/alt-1"] == [A], (shots, out[-1500:]))
        check("assign_B_gets_a_firing_account", [A] in (shots["W1/primary"], shots["W2/business"])
              and [B] in (shots["W1/primary"], shots["W2/business"]), shots)


def test_fair_share_e2e_sitter_never_reserved_beside_firing():
    """Verifier #4 C5(i): a lone flip with W1 holding a live cart elsewhere races
    W2 + W3 only -- W1 is not reserved to sit out."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_HELD_CART_REENTRY": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st(), D: _st(win_ago=600.0)}, gate)
        ws[0].purchase_executor._held_cart = {"tcin": D, "created": time.time() - 30}
        try:
            out = _flip_events(m, [A])
            time.sleep(0.3)
            shots = _shots(ws)
            res = dict(m._worker_reservations)
        finally:
            _settle(m, gate, ws)
        check("lone_sitter_not_reserved", "W1/primary" not in res and shots["W1/primary"] == [], (res, shots))
        check("lone_others_race", shots["W2/business"] == [A] and shots["W3/alt-1"] == [A], shots)


def test_all_free_sit_out_idle_fleet_is_a_miss():
    """Verifier #4 C5(ii): no race running, every account holds a live cart for C ->
    D is a MISS that stays on offer, never an empty race."""
    for flag in ("1", None):
        with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": flag,
                    "TARGET_MULTI_SKU_MISS_REARM": flag, "TARGET_HELD_CART_REENTRY": "1"}):
            gate = threading.Event()
            m, ws = _e2e_mgr({C: _st(win_ago=600.0), D: _st()}, gate)
            for w in ws:
                w.purchase_executor._held_cart = {"tcin": C, "created": time.time() - 30}
            m._states[D] = {"status": "ready"}
            try:
                out = _flip_events(m, [D])
                time.sleep(0.3)
                shots = _shots(ws)
                row = dict(m._states.get(D) or {})
            finally:
                _settle(m, gate, ws)
            if flag:
                check("idle_sitout_no_race", all(v == [] for v in shots.values()) and "[RACE]" not in out,
                      (shots, out[-800:]))
                check("idle_sitout_logged", "every free worker would sit it out" in out, out[-800:])
                check("idle_sitout_hinted", bool(row.get("rearm_hint_ts")), row)
            else:
                check("idle_sitout_off_dispatches_HEAD", "[RACE]" in out, out[-800:])


def test_purchase_recorded_on_every_path():
    """Verifier #4 C2(a)(c): a single-worker purchase and an ambiguous commit count
    as bought, so no FX-1006 path re-offers the TCIN in the same window."""
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        for name, res, want in (("single_success", {"success": True, "order_number": "X1", "quantity": 1}, True),
                                ("ambiguous_not_bought_HEAD_parity",
                                 {"success": False, "ambiguous_commit": True, "reason": "x"}, False)):
            m = _mgr(states={A: {"status": "attempting", "started_at": time.time()}}, statuses={A: _st()})
            try:
                quiet(m._update_purchase_result, A, res)
            except Exception:
                pass
            check(f"recorded_{name}", m._fx1006_recently_purchased(A) is want,
                  getattr(m, "_fx1006_purchase_ts", None))
        m = _mgr(states={A: {"status": "attempting", "started_at": time.time()}}, statuses={A: _st()})
        try:
            quiet(m._update_purchase_result, A, {"success": False, "reason": "rate_limited_429"})
        except Exception:
            pass
        check("not_recorded_plain_failure", not m._fx1006_recently_purchased(A))
    with env(**FLAGS):
        m = _mgr(states={A: {"status": "attempting", "started_at": time.time()}}, statuses={A: _st()})
        try:
            quiet(m._update_purchase_result, A, {"success": True, "order_number": "X1"})
        except Exception:
            pass
        check("not_recorded_flags_off", not getattr(m, "_fx1006_purchase_ts", None))


def test_keep_skips_bought_this_window():
    """Verifier #4 C2(b): KEEP must not hold a 'failed' row we bought this window
    (the level re-arm would re-race it); it resets exactly as HEAD."""
    with env(**{**FLAGS, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": "1"}):
        m = _mgr(states={A: {"status": "failed", "completed_at": time.time() - 60}, C: {"status": "ready"}},
                 statuses={A: _st(win_ago=120), C: _st()})
        m._fx1006_note_purchase(A)
        quiet(_reset, m, {C: {"in_stock": True}})
        check("keep_bought_this_window_reset", _rows(m)[A]["status"] == "ready", _rows(m)[A])


def test_miss_log_throttled():
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m, stop = _miss_mgr()
        try:
            _, o1 = quiet(m.process_stock_data, EV_B)
            _, o2 = quiet(m.process_stock_data, EV_B)
        finally:
            stop.set()
        check("miss_line_first_printed", "[MULTI_SKU_MISS]" in o1, o1[-400:])
        check("miss_line_repeat_suppressed", "[MULTI_SKU_MISS]" not in o2, o2[-400:])
        check("miss_repeat_still_restamps", bool(_rows(m)[B].get("rearm_hint_ts")), _rows(m)[B])
    with env(**{**FLAGS, **MSKU}):
        m, stop = _miss_mgr()
        try:
            _, o1 = quiet(m.process_stock_data, EV_B)
            _, o2 = quiet(m.process_stock_data, EV_B)
        finally:
            stop.set()
        check("miss_line_flag_off_every_time", "[MULTI_SKU_MISS]" in o1 and "[MULTI_SKU_MISS]" in o2)


def test_contender_scoring_is_silent():
    """Verifier #4: scoring a contender must not print the dispatch-skip lines of
    _all_dispatch_candidates_latched (they would claim a TCIN was not dispatched)."""
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_PARK_ACCOUNT_TCINS": f"primary:{B};business:{B};alt-1:{B}"}):
        m = _mgr(statuses={A: _st(), B: _st()})
        n, out = quiet(_fair, m, A)
        check("silent_all_parked_B_not_contender", n == 3, n)
        check("silent_no_dispatch_skip_line", "[DISPATCH_SKIP]" not in out and "not dispatching" not in out, out)


def test_held_cart_past_ttl_is_not_a_sit_out():
    m = _mgr(statuses={A: _st(), D: _st(win_ago=300.0)})
    w1 = m.worker_pool._w[0]
    with env(TARGET_HELD_CART_REENTRY="1", TARGET_HELD_CART_TTL_S="900"):
        w1.purchase_executor._held_cart = {"tcin": D, "created": time.time() - 2000}
        check("held_past_ttl_not_sit_out", m._fx1006_worker_sits_out(w1, A) is False)
        w1.purchase_executor._held_cart = {"tcin": D, "created": time.time() - 100}
        check("held_within_ttl_sit_out", m._fx1006_worker_sits_out(w1, A) is True)


def test_yield_single_pending_frees_every_waiting_racer():
    """A fresh skipped TCIN takes every racer waiting out an old-window re-entry."""
    ws = [H._Worker(1, "primary", [EDGE]), H._Worker(2, "business", [EDGE]), H._Worker(3, "alt-1", [EDGE])]
    m = H.race_mgr(ws)
    m._states[B] = {"status": "ready"}
    _with_checker(m, {A: _st(win_ago=600.0), B: _st(win_ago=1.0)})
    now = time.time()
    m._fx1006_miss_pending = {B: {"first_miss_ts": now, "window_start": now - 1.0}}
    with env(**{**FLAGS, **RACE_ENV, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1",
                "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        t0 = time.time()
        ok, out = quiet(H._race, m, A)
        el = time.time() - t0
    check("single_pending_all_three_yield", ok and el < 12.0 and out.count(f"skipped live TCIN {B}") == 3,
          (ok, el, out[-1200:]))


def test_stale_failed_rows_do_not_cancel_fair_share():
    """Verifier #5 C3 repro: B and C still carry 'failed' rows from an EARLIER window
    (no event ran while they were out of stock) and flip in the same read as A. The
    stock-aware reset must clear them so each of A, B, C gets a t+0 first shot."""
    for keep in ("1", None):
        with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                    "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": keep}):
            gate = threading.Event()
            m, ws = _e2e_mgr({A: _st(), B: _st(), C: _st()}, gate)
            old = time.time() - 900
            m._states[B] = {"status": "failed", "completed_at": old}
            m._states[C] = {"status": "failed", "completed_at": old}
            try:
                out = _flip_events(m, [A, B, C])
                time.sleep(0.3)
                fired = sorted(t for v in _shots(ws).values() for t in v)
            finally:
                _settle(m, gate, ws)
            tag = "keep_on" if keep else "keep_off"
            check(f"stale_failed_{tag}_each_one_first_shot", fired == sorted([A, B, C]), (fired, out[-1500:]))


def test_fresh_window_first_in_multi_tcin_events():
    """Verifier #5 C4 repro: one re-arm event holds O (old window, hinted, never
    dispatched) and F (fresh window). F must be served first and fully."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({O_: _st(win_ago=60.0), F_: _st(win_ago=3.0)}, gate)
        now = time.time()
        m._states[O_] = {"status": "ready", "rearm_hint_ts": now - 2}
        m._states[F_] = {"status": "ready", "rearm_hint_ts": now - 1}
        m._fx1006_last_dispatch = {F_: now - 600}      # F was raced 10 min ago, O never
        ev = {O_: {"in_stock": True, "title": "O"}, F_: {"in_stock": True, "title": "F"}}
        try:
            _, o1 = quiet(m.reset_completed_purchases_by_stock_status, ev)
            _, o2 = quiet(m.process_stock_data, ev)
            time.sleep(0.3)
            fired = sorted(t for v in _shots(ws).values() for t in v)
        finally:
            _settle(m, gate, ws)
        check("fresh_first_F_gets_all_three", fired == [F_, F_, F_], (fired, (o1 + o2)[-1500:]))


def test_yield_first_shots_only_no_ping_pong():
    """Verifier #5: an account never yields back to a TCIN it was already sent to
    in that TCIN's current window."""
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        m = _yield_mgr(pending_ago=4.0, b_status={"win_ago": 4.0})
        m._fx1006_acct_dispatch = {("W1/primary", B): time.time() - 1.0}
        check("pingpong_already_sent_no_yield", m._wave_yield_target(A, "W1/primary") is None)
        check("pingpong_other_account_yields", m._wave_yield_target(A, "W2/business") == B)
        m = _yield_mgr(pending_ago=4.0, b_status={"win_ago": 4.0})
        m._fx1006_acct_dispatch = {("W1/primary", B): time.time() - 600.0}   # an earlier window
        check("pingpong_earlier_window_ok", m._wave_yield_target(A, "W1/primary") == B)


def test_acct_dispatch_recorded_on_reservation():
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st()}, gate)
        try:
            _flip_events(m, [A])
            time.sleep(0.2)
            rec = dict(getattr(m, "_fx1006_acct_dispatch", {}) or {})
        finally:
            _settle(m, gate, ws)
        check("acct_dispatch_recorded", {k for k in rec} == {("W1/primary", A), ("W2/business", A),
                                                               ("W3/alt-1", A)}, rec)


def test_single_ready_worker_sit_out_is_a_miss():
    """Verifier #5 C5: with ONE ready worker that holds a live cart for C, a flip of D
    is a MISS, not an empty race."""
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_HELD_CART_REENTRY": "1"}):
        m = _mgr(states={D: {"status": "ready"}}, statuses={C: _st(win_ago=600.0), D: _st()})
        m.worker_pool = _Pool(_fleet()[:1])
        m.worker_pool._w[0].purchase_executor._held_cart = {"tcin": C, "created": time.time() - 30}
        _, out = quiet(m.process_stock_data, {D: {"in_stock": True, "title": "D"}})
        check("one_worker_sitout_no_dispatch", m.started == [], (m.started, out[-500:]))
        check("one_worker_sitout_hinted", bool(_rows(m)[D].get("rearm_hint_ts")), _rows(m)[D])


def test_miss_throttle_is_per_window():
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m, stop = _miss_mgr()
        try:
            _, o1 = quiet(m.process_stock_data, EV_B)
            m.stock_monitor._resilient_checker._tcin_status[B] = _st(win_ago=0.1)   # a NEW window
            m.stock_monitor._resilient_checker._tcin_status[B].window_start_at = time.time() + 1.0
            _, o2 = quiet(m.process_stock_data, EV_B)
        finally:
            stop.set()
        check("miss_new_window_prints_again", "[MULTI_SKU_MISS]" in o1 and "[MULTI_SKU_MISS]" in o2, (o1[-300:], o2[-300:]))


def test_ac1_latch_is_per_identity_and_tcin_stays_offered():
    """Verifier #6 C5: W1 is AC-1-latched on X, W2/W3 race A. X flips: the only free
    account (W1) sits it out, so X is a MISS -- and it must stay HINTED (re-offered),
    so W2/W3 can race it when they free (HEAD parity: the latch is per identity)."""
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_AMBIGUOUS_COMMIT_LATCH": "1"}):
        X = "1010000008"
        m = _mgr(states={X: {"status": "ready"}}, statuses={A: _st(), X: _st()})
        quiet(m._ac_latch_mark, "W1/primary", X, "execution_timeout")
        check("ac1_latch_not_bought", not m._fx1006_recently_purchased(X))
        stop = threading.Event()
        th = threading.Thread(target=stop.wait, daemon=True)
        th.start()
        now = time.time()
        m._states[A] = {"status": "attempting", "started_at": now - 5, "real_purchase": True, "race": True}
        m._active_purchases = {f"{A}#W2": {"thread": th, "started_at": now - 5, "status": "running",
                                           "worker": "W2/business"},
                               f"{A}#W3": {"thread": th, "started_at": now - 5, "status": "running",
                                           "worker": "W3/alt-1"}}
        m._worker_reservations = {"W2/business": (A, now - 5), "W3/alt-1": (A, now - 5)}
        try:
            _, out = quiet(m.process_stock_data, {X: {"in_stock": True, "title": "X"}})
        finally:
            stop.set()
        check("ac1_X_not_dispatched", m.started == [], (m.started, out[-500:]))
        check("ac1_X_hinted_for_rearm", bool(_rows(m)[X].get("rearm_hint_ts")), _rows(m)[X])
        check("ac1_no_pursuing_line_before_sitout", "pursuing alongside" not in out, out[-500:])


def test_phantom_contender_only_busy_accounts_could_race():
    """Verifier #5: B is fresh, but only W1 (busy on X) could race it -- W2/W3 are
    parked on B. B must not hold back either free account from A."""
    X = "1010000009"
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_PARK_ACCOUNT_TCINS": f"business:{B};alt-1:{B}"}):
        m = _mgr(statuses={A: _st(), B: _st()})
        m._worker_reservations = {"W1/primary": (X, time.time())}
        check("phantom_contender_ignored", _fair(m, A) == 3, _fair(m, A))


def test_read_gap_renewal_is_not_a_flip():
    """Verifier #6: a >20 s read gap renews window_start with no out-of-stock read.
    Not a fresh contender, and it does not lapse the bought guard."""
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m = _mgr(statuses={A: _st(), B: _st(win_ago=1.0, lf_ago=300.0)})
        check("gap_renewal_not_contender", _fair(m, A) == 3)
        m._fx1006_purchase_ts = {B: time.time() - 120.0}      # bought in the "previous" window
        check("gap_renewal_keeps_bought_guard", m._fx1006_recently_purchased(B))
        m = _mgr(statuses={B: _st(win_ago=1.0)})               # a genuine flip since the purchase
        m._fx1006_purchase_ts = {B: time.time() - 120.0}
        check("genuine_flip_lapses_bought_guard", not m._fx1006_recently_purchased(B))
        m = _mgr(statuses={B: _st(win_ago=300.0, lf_ago=3.0)})  # a flicker: OOS read, same window
        m._fx1006_purchase_ts = {B: time.time() - 120.0}
        check("flicker_keeps_bought_guard", m._fx1006_recently_purchased(B))


def test_old_window_defers_to_fresh_flip_same_sweep():
    """Verifier #6 C4: an old-window flicker O comes BEFORE fresh F in the same
    response. O defers (hinted, re-offered); F's first volley gets all 3."""
    for flag in ("1", None):
        with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": flag,
                    "TARGET_MULTI_SKU_MISS_REARM": flag}):
            gate = threading.Event()
            m, ws = _e2e_mgr({O_: _st(win_ago=90.0, lf_ago=1.0), F_: _st(win_ago=0.3)}, gate)
            m._states[O_] = {"status": "ready"}
            try:
                out = _flip_events(m, [O_, F_])
                time.sleep(0.3)
                fired = sorted(t for v in _shots(ws).values() for t in v)
                o_row = dict(m._states.get(O_) or {})
            finally:
                _settle(m, gate, ws)
            if flag:
                check("defer_F_gets_all_three", fired == [F_, F_, F_], (fired, out[-1500:]))
                check("defer_O_hinted", bool(o_row.get("rearm_hint_ts")), o_row)
                check("defer_logged", "deferring to fresh" in out, out[-1500:])
            else:
                check("defer_off_O_takes_all_HEAD", fired == [O_, O_, O_], fired)


def test_no_yield_from_legacy_single_worker_path():
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        m = _yield_mgr()
        check("legacy_auto_never_yields", m._wave_yield_target(A, "auto") is None)


def test_in_delay_failed_message_not_an_error():
    ev = {A: {"in_stock": True, "title": "A"}}
    for flag, want_err in (("1", False), (None, True)):
        with env(**{**FLAGS, **MSKU, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": flag}):
            m = _mgr(states={A: {"status": "failed", "completed_at": time.time() - 1.0}},
                     statuses={A: _st(win_ago=60)})
            _, out = quiet(m.process_stock_data, ev)
            tag = "on" if flag else "off"
            check(f"in_delay_{tag}_error_{want_err}", ("[PURCHASE_ERROR]" in out) is want_err, out[-400:])
            if flag:
                check("in_delay_on_info_line", "inside its error delay" in out, out[-400:])


def test_fresh_first_sort_without_fair_share():
    """The fresh-first order stands on its own (MISS on, FAIR off): in one re-arm
    event F (fresh) is served before O (old, never dispatched)."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({O_: _st(win_ago=60.0), F_: _st(win_ago=3.0)}, gate)
        now = time.time()
        m._states[O_] = {"status": "ready", "rearm_hint_ts": now - 2}
        m._states[F_] = {"status": "ready", "rearm_hint_ts": now - 1}
        m._fx1006_last_dispatch = {F_: now - 600}
        ev = {O_: {"in_stock": True, "title": "O"}, F_: {"in_stock": True, "title": "F"}}
        try:
            quiet(m.reset_completed_purchases_by_stock_status, ev)
            _, out = quiet(m.process_stock_data, ev)
            time.sleep(0.3)
            fired = sorted(t for v in _shots(ws).values() for t in v)
        finally:
            _settle(m, gate, ws)
        check("sort_alone_fresh_F_first", fired == [F_, F_, F_], (fired, out[-1200:]))


def test_stale_read_all_sit_out_stays_offered():
    """Verifier #7: a 20 s old read (past PROBE_FRESH 15 s, inside the re-arm's 90 s)
    of a TCIN every free account sits out must still be hinted and re-offered."""
    from app import StockMonitorThread
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_HELD_CART_REENTRY": "1"}):
        m = _mgr(states={D: {"status": "ready"}}, statuses={C: _st(win_ago=600.0), D: _st(age=20.0, win_ago=25.0)})
        for w in m.worker_pool._w:
            w.purchase_executor._held_cart = {"tcin": C, "created": time.time() - 30}
        _, out = quiet(m.process_stock_data, {D: {"in_stock": True, "title": "D"}})
        check("stale_sitout_not_dispatched", m.started == [], (m.started, out[-400:]))
        check("stale_sitout_hinted", bool(_rows(m)[D].get("rearm_hint_ts")), _rows(m)[D])
        rearm = StockMonitorThread._build_level_rearm_map(
            {D: m.stock_monitor._resilient_checker._tcin_status[D]}, m.get_all_states(), time.time())
        check("stale_sitout_rearm_offers", D in rearm, rearm)


def test_old_window_keeps_accounts_fresh_cannot_use():
    """Verifier #7: O (old window) before fresh F; business + alt-1 are parked on F.
    O races with exactly those two; F gets primary -- nobody idles."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_PARK_ACCOUNT_TCINS": f"business:{F_};alt-1:{F_}"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({O_: _st(win_ago=90.0, lf_ago=1.0), F_: _st(win_ago=0.3)}, gate)
        m._states[O_] = {"status": "ready"}
        try:
            out = _flip_events(m, [O_, F_])
            time.sleep(0.3)
            shots = _shots(ws)
        finally:
            _settle(m, gate, ws)
        check("partial_defer_O_on_parked_pair", shots["W2/business"] == [O_] and shots["W3/alt-1"] == [O_],
              (shots, out[-1500:]))
        check("partial_defer_F_on_primary", shots["W1/primary"] == [F_], shots)
    # only alt-1 cannot serve F: O takes alt-1 alone, F keeps BOTH others (an even
    # split would have given O two and F one)
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_PARK_ACCOUNT_TCINS": f"alt-1:{F_}"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({O_: _st(win_ago=90.0, lf_ago=1.0), F_: _st(win_ago=0.3)}, gate)
        m._states[O_] = {"status": "ready"}
        try:
            out = _flip_events(m, [O_, F_])
            time.sleep(0.3)
            shots = _shots(ws)
        finally:
            _settle(m, gate, ws)
        check("partial_defer_O_only_spare", shots["W3/alt-1"] == [O_], (shots, out[-1500:]))
        check("partial_defer_F_keeps_two", shots["W1/primary"] == [F_] and shots["W2/business"] == [F_], shots)


def test_line_holder_goes_to_its_tcin():
    """Verifier #7 (FX-1001-A): primary still holds a left-behind won line for B.
    A and B flip together: A's slots go to the others, primary goes to B."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        gate = threading.Event()
        m, ws = _e2e_mgr({A: _st(), B: _st()}, gate)
        ws[0].purchase_executor._woncart_dirty = {"tcin": B, "ts": time.time() - 60, "why": "x"}
        try:
            out = _flip_events(m, [A, B])
            time.sleep(0.3)
            shots = _shots(ws)
        finally:
            _settle(m, gate, ws)
        check("line_holder_primary_on_B", shots["W1/primary"] == [B], (shots, out[-1500:]))
        check("line_holder_A_on_others", sorted(shots["W2/business"] + shots["W3/alt-1"]) == [A, A], shots)


def test_pingpong_guard_survives_read_gap():
    """Verifier #7: a read gap renewed B's window_start with no out-of-stock read;
    an account dispatched to B 40 s ago must not yield back to it."""
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}):
        m = _yield_mgr(pending_ago=5.0, b_status={"win_ago": 5.0, "lf_ago": 300.0})
        m._fx1006_acct_dispatch = {("W1/primary", B): time.time() - 40.0}
        check("pingpong_gap_no_yield", m._wave_yield_target(A, "W1/primary") is None)


def test_all_latched_alert_still_printed():
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_AMBIGUOUS_COMMIT_LATCH": "1"}):
        m = _mgr(states={D: {"status": "ready"}}, statuses={D: _st()})
        for w in m.worker_pool._w:
            quiet(m._ac_latch_mark, w.label(), D, "execution_timeout")
        _, out = quiet(m.process_stock_data, {D: {"in_stock": True, "title": "D"}})
        check("latched_alert_kept", "every candidate identity is" in out and m.started == [], (m.started, out[-600:]))


def test_latched_line_holder_is_not_held_for_its_line():
    """Verifier #8 case 1: business holds a left-behind line for G AND is AC-1-latched
    on G. F and G flip together: nobody may idle -- business fires F with one more
    account, the third fires G (HEAD: all three on F)."""
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_AMBIGUOUS_COMMIT_LATCH": "1"}):
        G_ = "1010000007"
        gate = threading.Event()
        m, ws = _e2e_mgr({F_: _st(), G_: _st()}, gate)
        ws[1].purchase_executor._woncart_dirty = {"tcin": G_, "ts": time.time() - 60, "why": "x"}
        quiet(m._ac_latch_mark, "W2/business", G_, "ambiguous_commit")
        try:
            out = _flip_events(m, [F_, G_])
            time.sleep(0.3)
            shots = _shots(ws)
        finally:
            _settle(m, gate, ws)
        fired = sorted(t for v in shots.values() for t in v)
        check("latched_holder_nobody_idle", all(len(v) == 1 for v in shots.values()), (shots, out[-1500:]))
        check("latched_holder_on_F", shots["W2/business"] == [F_], shots)
        check("latched_holder_G_served", fired.count(G_) == 1 and fired.count(F_) == 2, fired)


def test_hold_back_only_for_a_contender_a_leftover_can_fire():
    """Verifier #8 case R1b: primary holds a line for F, business is AC-1-latched on G,
    alt-1 is busy. Holding business back "for G" would idle it: F takes both."""
    X = "1010000009"
    with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_AMBIGUOUS_COMMIT_LATCH": "1"}):
        G_ = "1010000007"
        gate = threading.Event()
        m, ws = _e2e_mgr({F_: _st(), G_: _st(), X: _st(win_ago=300.0)}, gate)
        ws[0].purchase_executor._woncart_dirty = {"tcin": F_, "ts": time.time() - 60, "why": "x"}
        quiet(m._ac_latch_mark, "W2/business", G_, "ambiguous_commit")
        stop = threading.Event()
        th = threading.Thread(target=stop.wait, daemon=True)
        th.start()
        now = time.time()
        m._states[X] = {"status": "attempting", "started_at": now - 5, "real_purchase": True, "race": True}
        m._active_purchases[f"{X}#W3"] = {"thread": th, "started_at": now - 5, "status": "running",
                                          "worker": "W3/alt-1"}
        m._reserve_state()                                   # materialise the reservation map
        m._worker_reservations["W3/alt-1"] = (X, now - 5)
        try:
            out = _flip_events(m, [F_, G_])
            time.sleep(0.3)
            shots = _shots(ws)
        finally:
            stop.set()
            _settle(m, gate, ws)
        check("holdback_F_gets_primary_and_business",
              shots["W1/primary"] == [F_] and shots["W2/business"] == [F_], (shots, out[-1500:]))


def test_two_line_holders_for_the_first_tcin_both_go_there():
    """Verifier #9 CE-1 / CE-2: primary and business both hold a line for F (a live held
    cart, or a left-behind won line); F, G, H flip together. Both must go to F (the
    line is re-entered / struck there, never deleted elsewhere), alt-1 to G."""
    G_, H_ = "1010000007", "1010000010"
    for kind in ("held", "dirty"):
        with env(**{**FLAGS, **MSKU, **E2E_ENV, "TARGET_MULTI_SKU_FAIR_SHARE": "1",
                    "TARGET_MULTI_SKU_MISS_REARM": "1", "TARGET_HELD_CART_REENTRY": "1"}):
            gate = threading.Event()
            m, ws = _e2e_mgr({F_: _st(), G_: _st(), H_: _st()}, gate)
            for w in ws[:2]:
                if kind == "held":
                    w.purchase_executor._held_cart = {"tcin": F_, "created": time.time() - 60}
                else:
                    w.purchase_executor._woncart_dirty = {"tcin": F_, "ts": time.time() - 120, "why": "x"}
            try:
                out = _flip_events(m, [F_, G_, H_])
                time.sleep(0.3)
                shots = _shots(ws)
            finally:
                _settle(m, gate, ws)
            check(f"two_{kind}_holders_both_on_F", shots["W1/primary"] == [F_] and shots["W2/business"] == [F_],
                  (shots, out[-1500:]))
            check(f"two_{kind}_holders_alt1_on_G", shots["W3/alt-1"] == [G_], shots)


def test_yield_flag_off_source_pin():
    src = (ROOT / "src/purchasing/bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    i = src.index("if _wave_yield_on():\n                                # FX-1006-YIELD")
    seg = src[i:i + 700]
    check("pin_yield_else_plain_sleep", "else:\n                                time.sleep(_re_s)" in seg, seg)


# ── 5. FX-1006-LOCK (source pin) ─────────────────────────────────────────────

def test_lock_order_source_pin():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    i = src.index("if os.environ.get('TARGET_SHARED_LOCK_ORDER_FIX'")
    seg = src[i:i + 600]
    on = seg[:seg.index("else:")]
    check("pin_lock_snapshot_before_lock",
          on.index("get_all_states()") < on.index("with shared_data.lock:"), on)
    check("pin_lock_off_branch_prior_code",
          "with shared_data.lock:\n                    shared_data.purchase_states = "
          "self.purchase_manager.get_all_states()" in seg, seg)
    # Every `with shared_data.lock:` block in app.py: the only one that calls into
    # the purchase manager is the flag-off branch above.
    lines = src.splitlines()
    nested = []
    for n, ln in enumerate(lines):
        if ln.strip() == "with shared_data.lock:":
            ind = len(ln) - len(ln.lstrip())
            for body in lines[n + 1:]:
                if body.strip() and len(body) - len(body.lstrip()) <= ind:
                    break
                if "purchase_manager." in body or "get_all_states" in body or "_state_lock" in body:
                    nested.append((n + 1, lines[n - 1].strip()))
                    break
    check("pin_lock_only_nested_site_is_flag_off_branch",
          len(nested) == 1 and nested[0][1] == "else:", nested)
    i = src.index("FX-1006-KEEP: a 'failed' row ABSENT from this event")
    check("pin_keep_verification_skip_before_check",
          i < src.index("if is_in_stock:", i) and "TARGET_STOCK_RESET_KEEP_LIVE_FAILED" in src[i:i + 700])
    check("pin_keep_verification_skips_error_delay",
          "time.time() - float(after_state.get('completed_at') or 0) < 10.5" in src[i:i + 900])


# ── 9. FAIR-FRESH-1015 (TARGET_FAIR_FRESH_FIX) ───────────────────────────────
# 10-09 window 9 (run_20261009_000934, TCIN 1013166576): the newest flip B flickered
# out of stock 0.4 s into its window, so the freshness test (which read the LATEST
# out-of-stock read) called it old; the wave A raced 17 s earlier -- failed, then
# emergency-reset to 'ready' -- still counted as a fresh, un-raced FAIR contender, so
# B was deferred behind it: first shot +6.7 s. A = the raced wave (573), B = the newest
# flip (576); replay_w9.py V0..V4 are the cases below.

FF = "TARGET_FAIR_FRESH_FIX"
W9 = {**MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", "TARGET_MULTI_SKU_MISS_REARM": "1",
      "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": "1", "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}
EV_W9_B = {B: {"in_stock": True, "title": "B", "max_qty": 2}}
EV_W9_AB = {B: {"in_stock": True, "title": "B", "max_qty": 2}, A: {"in_stock": True, "title": "A", "max_qty": 2}}


def _flicker(b):
    """The in-window out-of-stock read of the newest flip: after the window opened,
    before the latest in-stock read (so the latest read still reads in stock)."""
    b.last_false_at = b.last_true_at - 0.15


def _w9_state(m_factory, a_row=None, a_win_ago=17.0, a_dispatched=True, flicker=True):
    """The w9 state, inside the caller's env: A raced in its window and was reset to
    'ready' with a hint; B's window is 0.75 s old and its flip callback has just seen
    it (first sight) -- then the flicker read lands."""
    now = time.time()
    a = _st(win_ago=a_win_ago, lf_ago=a_win_ago + 0.1)
    b = _st(win_ago=0.75)                          # genuine flip: last OOS read 0.34 s before the open
    row = dict(a_row) if a_row is not None else {"status": "ready", "rearm_hint_ts": now - 0.1}
    m = m_factory({A: row, B: {"status": "ready", "rearm_hint_ts": now - 0.7}}, {A: a, B: b})
    if a_dispatched:
        m._fx1006_dict("_fx1006_last_dispatch")[A] = now - a_win_ago
    m.stock_snapshot(A)
    m.stock_snapshot(B)                            # the flip callback's first sight of B's window
    if flicker:
        _flicker(b)
    return m, a, b


def _w9_view(m):
    now = time.time()
    return (bpm_mod._fx1006_fresh_snap(m.stock_snapshot(B), now),
            sorted(m._fx1006_defer_for_fresh(B)),
            sorted(m._fair_share_contenders(B, set(), m.worker_pool.ready_workers())))


def test_fair_fresh_flag_default_off():
    for v in (None, "", "0", "true", "yes", "01", "2", "junk", "on"):
        with env(**{FF: v}):
            check(f"fresh_flag_off_{v!r}", bpm_mod._fair_fresh_on() is False)
    for v in ("1", " 1 "):
        with env(**{FF: v}):
            check(f"fresh_flag_on_{v!r}", bpm_mod._fair_fresh_on() is True)


def test_fair_fresh_w9_replay_flag_off_is_old_behaviour():
    """Flag unset / junk: V1 reproduces the 10-09 deferral, the snapshot dict has no
    new key and nothing is stored (byte-identical)."""
    for flag in (None, "0", "true"):
        with env(**{**FLAGS, **W9, FF: flag}):
            m, a, b = _w9_state(lambda s, st: _mgr(states=s, statuses=st))
            fresh, defer_b, cont_b = _w9_view(m)
            check(f"w9_off_{flag}_B_not_fresh", fresh is False)
            check(f"w9_off_{flag}_B_defers_to_A", defer_b == [A], defer_b)
            check(f"w9_off_{flag}_A_is_contender", cont_b == [A], cont_b)
            _, out = quiet(m.process_stock_data, EV_W9_B)
            check(f"w9_off_{flag}_V1_started_nothing", m.started == [], (m.started, out[-600:]))
            check(f"w9_off_{flag}_no_pre_key", "pre_false" not in m.stock_snapshot(B))
            check(f"w9_off_{flag}_nothing_stored", not hasattr(m, "_fair_fresh_pre")
                  and not hasattr(m, "_fair_fresh_noshot"))
    # V2 (tick event with both): A goes first and takes the fleet; V3, V4 are unchanged
    with env(**{**FLAGS, **W9}):
        m, _, _ = _w9_state(lambda s, st: _mgr(states=s, statuses=st))
        quiet(m.process_stock_data, EV_W9_AB)
        check("w9_off_V2_A_first", m.started[:1] == [A], m.started)


def test_fair_fresh_w9_replay_flag_on():
    with env(**{**FLAGS, **W9, FF: "1"}):
        # V0: B without a flicker -- fresh with or without the fix
        m, _, _ = _w9_state(lambda s, st: _mgr(states=s, statuses=st), flicker=False)
        fresh, defer_b, cont_b = _w9_view(m)
        check("w9_on_V0_fresh", fresh is True and defer_b == [] and cont_b == [], (fresh, defer_b, cont_b))
        # V1: B flickered, event carries B only
        m, a, b = _w9_state(lambda s, st: _mgr(states=s, statuses=st))
        fresh, defer_b, cont_b = _w9_view(m)
        check("w9_on_V1_B_fresh", fresh is True)
        check("w9_on_V1_B_not_deferred", defer_b == [], defer_b)
        check("w9_on_V1_raced_A_not_a_contender", cont_b == [], cont_b)
        _, out = quiet(m.process_stock_data, EV_W9_B)
        check("w9_on_V1_B_started", m.started == [B], (m.started, out[-600:]))
        check("w9_on_V1_B_dispatch_stamped", B in m._fx1006_dict("_fx1006_last_dispatch"))
        # V2: the tick event carries A and B -- B first (A is dispatched in its window)
        m, _, _ = _w9_state(lambda s, st: _mgr(states=s, statuses=st))
        quiet(m.process_stock_data, EV_W9_AB)
        check("w9_on_V2_B_first_then_A", m.started == [B, A], m.started)
        # V3: A's row still 'failed' -- not a contender either way; B goes
        m, _, _ = _w9_state(lambda s, st: _mgr(states=s, statuses=st),
                            a_row={"status": "failed", "completed_at": time.time() - 0.6})
        quiet(m.process_stock_data, EV_W9_B)
        check("w9_on_V3_B_started", m.started == [B], m.started)
        # V4: A's window is 22.4 s old -- stale, not a contender; both go, B first
        m, _, _ = _w9_state(lambda s, st: _mgr(states=s, statuses=st), a_win_ago=22.4)
        quiet(m.process_stock_data, EV_W9_AB)
        check("w9_on_V4_both_B_first", m.started == [B, A], m.started)
        # Part A alone is enough for B to be dispatched, Part B alone too (the other
        # half is neutralised by hand): B's freshness and A's non-contender status
        m, a, b = _w9_state(lambda s, st: _mgr(states=s, statuses=st))
        m._fx1006_dict("_fair_fresh_pre").clear()                  # no stamp -> today's freshness for B
        check("w9_partB_alone_B_not_fresh", bpm_mod._fx1006_fresh_snap(m.stock_snapshot(B), time.time()) is False)
        check("w9_partB_alone_A_not_contender",
              m._fair_share_contenders(B, set(), m.worker_pool.ready_workers()) == [])
        m, a, b = _w9_state(lambda s, st: _mgr(states=s, statuses=st), a_dispatched=False)
        check("w9_partA_alone_B_fresh", bpm_mod._fx1006_fresh_snap(m.stock_snapshot(B), time.time()) is True)
        check("w9_partA_alone_A_still_contender",
              m._fair_share_contenders(B, set(), m.worker_pool.ready_workers()) == [A])


def test_fair_fresh_e2e_tick_first_volley_width():
    """V2 through the REAL dispatch: with the fix B's first volley is all 3 free
    accounts and A (raced already) gets none; without it A takes the fleet."""
    for flag in ("1", None):
        with env(**{**FLAGS, **W9, **E2E_ENV, FF: flag}):
            gate = threading.Event()
            now = time.time()
            a = _st(win_ago=17.0, lf_ago=17.1)
            b = _st(win_ago=0.75)
            m, ws = _e2e_mgr({A: a, B: b}, gate)
            m._states[A] = {"status": "ready", "rearm_hint_ts": now - 0.1}
            m._states[B] = {"status": "ready", "rearm_hint_ts": now - 0.7}
            m._fx1006_dict("_fx1006_last_dispatch")[A] = now - 17.0
            m.stock_snapshot(B)
            _flicker(b)
            try:
                _, out = quiet(m.process_stock_data, EV_W9_AB)
                time.sleep(0.3)
                shots = _shots(ws)
                a_row = dict(m._states.get(A) or {})
            finally:
                _settle(m, gate, ws)
            fired = sorted(t for v in shots.values() for t in v)
            if flag:
                check("e2e_on_B_gets_all_three", fired == [B, B, B], (shots, out[-1200:]))
                check("e2e_on_A_zero_accounts", not any(A in v for v in shots.values()), shots)
                check("e2e_on_A_stays_on_offer", bool(a_row.get("rearm_hint_ts")), a_row)
            else:
                check("e2e_off_A_takes_the_fleet", fired == [A, A, A], (shots, out[-1200:]))


def test_fair_fresh_unraced_contender_splits_with_flickered_flip():
    """A flickered newest flip B beside a genuinely fresh, UNRACED X: with Part A on
    they split by the FAIR cap (ceil(3/2) = 2) and neither is deferred; off, B reads
    old and defers to X."""
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", "TARGET_MULTI_SKU_MISS_REARM": "1", FF: "1"}):
        b = _st(win_ago=0.75)
        m = _mgr(statuses={B: b, C: _st(win_ago=2.0)})
        m.stock_snapshot(B)
        _flicker(b)
        check("split_B_fresh", m._fx1006_fresh_window(B) is True)
        check("split_B_not_deferred", m._fx1006_defer_for_fresh(B) == [])
        check("split_X_not_deferred", m._fx1006_defer_for_fresh(C) == [])
        check("split_cap_is_2", _fair(m, B) == 2, _fair(m, B))
        check("split_cap_X_is_2", _fair(m, C) == 2)
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        b = _st(win_ago=0.75)
        m = _mgr(statuses={B: b, C: _st(win_ago=2.0)})
        m.stock_snapshot(B)
        _flicker(b)
        check("split_off_B_defers_to_X", m._fx1006_defer_for_fresh(B) == [C])


def test_fair_fresh_pre_stamp_rules():
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", FF: "1"}):
        b = _st(win_ago=0.75)
        m = _mgr(statuses={B: b})
        pre = b.last_false_at
        s1 = m.stock_snapshot(B)
        check("pre_taken_at_first_sight", s1.get("pre_false") == pre, s1)
        _flicker(b)
        s2 = m.stock_snapshot(B)
        check("pre_survives_flicker", s2.get("pre_false") == pre and s2["last_false"] > s2["window_start"], s2)
        check("pre_flickered_flip_is_fresh", bpm_mod._fx1006_fresh_snap(s2, time.time()) is True)
        # first sight AFTER the flicker: no stamp -> today's answer (not fresh)
        b = _st(win_ago=0.75, lf_ago=0.35)
        m = _mgr(statuses={B: b})
        s = m.stock_snapshot(B)
        check("pre_absent_when_flicker_came_first", "pre_false" not in s, s)
        check("pre_absent_falls_back_not_fresh", bpm_mod._fx1006_fresh_snap(s, time.time()) is False)
        # never read out of stock: no stamp, not a flip
        b = _st(win_ago=0.75)
        b.last_false_at = 0.0
        m = _mgr(statuses={B: b})
        s = m.stock_snapshot(B)
        check("pre_absent_never_oos", "pre_false" not in s and not bpm_mod._fx1006_fresh_snap(s, time.time()), s)
        # a read-gap renewal (last OOS read 300 s before the open) is not a flip
        b = _st(win_ago=1.0, lf_ago=300.0)
        m = _mgr(statuses={B: b})
        s = m.stock_snapshot(B)
        check("pre_gap_renewal_stamped_but_not_fresh",
              s.get("pre_false") and bpm_mod._fx1006_fresh_snap(s, time.time()) is False, s)
        # an old window that flickers back stays old (no stamp: the flicker is after the open)
        b = _st(win_ago=300.0, lf_ago=3.0)
        m = _mgr(statuses={A: _st(), B: b})
        check("pre_old_window_flicker_back_not_contender", _fair(m, A) == 3)
        # a window past FAIR_FRESH_S is old even with a clean stamp
        m = _mgr(statuses={A: _st(), B: _st(win_ago=25.0)})
        check("pre_window_past_young_s_not_contender", _fair(m, A) == 3)
        # a NEW window of the same TCIN takes its own stamp
        b = _st(win_ago=30.0)
        m = _mgr(statuses={B: b})
        old = m.stock_snapshot(B)["pre_false"]
        t = time.time()
        b.window_start_at = t - 0.5
        b.last_false_at = t - 0.9
        b.last_true_at = b.last_checked_at = t - 0.1
        s = m.stock_snapshot(B)
        check("pre_new_window_own_stamp", s["pre_false"] == t - 0.9 != old, (s, old))
        _flicker(b)
        check("pre_new_window_fresh_after_flicker", bpm_mod._fx1006_fresh_snap(m.stock_snapshot(B), time.time()) is True)
        # one entry per TCIN: memory is bounded by the TCIN count
        check("pre_one_entry_per_tcin", len(m._fx1006_dict("_fair_fresh_pre")) == 1)
        # probe off / no checker: no window, no stamp, no exception
        m = _mgr(statuses={B: _st(win_ago=0.75)})
        m.stock_monitor = None
        check("pre_no_checker_no_stamp", "pre_false" not in m.stock_snapshot(B))
        with env(TARGET_STOCK_PROBE="0"):
            m = _mgr(statuses={B: _st(win_ago=0.75)})
            check("pre_probe_off_no_stamp", "pre_false" not in m.stock_snapshot(B))


def test_fair_fresh_snapshot_without_stamp_falls_back():
    """A snapshot with no usable pre-window stamp (older monitor, missing / None /
    NaN / inf / bool / text) keeps today's last_false behaviour and never raises."""
    now = time.time()
    genuine = {"age": 0.2, "last_true": now - 0.2, "last_false": now - 1.1, "window_start": now - 0.75}
    flicker = {"age": 0.2, "last_true": now - 0.2, "last_false": now - 0.35, "window_start": now - 0.75}
    with env(**{**FLAGS, FF: "1"}):
        for bad in (None, 0, 0.0, -1.0, float("nan"), float("inf"), "x", True, [], {}):
            g = dict(genuine, pre_false=bad)
            f = dict(flicker, pre_false=bad)
            check(f"fallback_{bad!r}_genuine_fresh", bpm_mod._fx1006_fresh_snap(g, now) is True)
            check(f"fallback_{bad!r}_flicker_not_fresh", bpm_mod._fx1006_fresh_snap(f, now) is False)
        check("fallback_key_missing", bpm_mod._fx1006_fresh_snap(dict(flicker), now) is False)
        check("pre_stamp_rescues_flicker", bpm_mod._fx1006_fresh_snap(dict(flicker, pre_false=now - 1.1), now) is True)
        check("pre_stamp_too_old_is_not_fresh",
              bpm_mod._fx1006_fresh_snap(dict(flicker, pre_false=now - 100.0), now) is False)
        check("pre_stamp_after_open_is_not_fresh",
              bpm_mod._fx1006_fresh_snap(dict(flicker, pre_false=now - 0.3), now) is False)
        check("fresh_snap_garbage_never_raises",
              bpm_mod._fx1006_fresh_snap(None, now) is False and bpm_mod._fx1006_fresh_snap({"age": "x"}, now) is False)
    with env(**{**FLAGS}):
        # flag off: the key is ignored entirely
        check("flag_off_ignores_pre_false", bpm_mod._fx1006_fresh_snap(dict(flicker, pre_false=now - 1.1), now) is False)


def test_fair_fresh_callbacks_take_first_sight():
    """The stock-aware reset and process_stock_data are the flip callbacks: each takes
    the window's pre-open stamp for the in-stock TCINs of the event (dict or list
    format) before any later flicker can overwrite last_false."""
    ev = {B: {"in_stock": True, "title": "B"}, C: {"in_stock": False, "title": "C"}}
    for label, call in (("reset_dict", lambda m: m.reset_completed_purchases_by_stock_status(ev)),
                        ("reset_list", lambda m: m.reset_completed_purchases_by_stock_status(
                            [{"tcin": B, "in_stock": True}, {"tcin": C, "in_stock": False}])),
                        ("process", lambda m: m.process_stock_data(ev))):
        with env(**{**FLAGS, **MSKU, FF: "1"}):
            b = _st(win_ago=0.75)
            pre = b.last_false_at
            m = _mgr(statuses={B: b, C: _st(in_stock=False)})
            quiet(call, m)
            _flicker(b)
            s = m.stock_snapshot(B)
            check(f"callback_{label}_pre_taken", s.get("pre_false") == pre, s)
            check(f"callback_{label}_oos_tcin_skipped", C not in m._fx1006_dict("_fair_fresh_pre"),
                  m._fx1006_dict("_fair_fresh_pre"))
        with env(**{**FLAGS, **MSKU}):
            b = _st(win_ago=0.75)
            m = _mgr(statuses={B: b, C: _st(in_stock=False)})
            quiet(call, m)
            check(f"callback_{label}_flag_off_inert", not hasattr(m, "_fair_fresh_pre"))
    with env(**{**FLAGS, FF: "1"}):
        m = _mgr(statuses={B: _st(win_ago=0.75)})
        check("prime_garbage_never_raises",
              all(m._fair_fresh_prime(x) is None for x in (None, 5, "x", [None, 3], {B: None}, {B: 1}, [{"tcin": None, "in_stock": True}])))


def test_fair_fresh_part_b_contender_rules():
    """Part B: a TCIN dispatched in its CURRENT window is no contender; one whose last
    dispatch was before window_start - 0.5 s (an earlier window) still is."""
    def mk(rel=None, noshot_rel=None, row=None, flag="1"):
        st = {A: _st(win_ago=17.0), B: _st(win_ago=0.5)}
        m = _mgr(states={A: row or {"status": "ready"}}, statuses=st)
        ws = st[A].window_start_at
        if rel is not None:
            m._fx1006_dict("_fx1006_last_dispatch")[A] = ws + rel
        if noshot_rel is not None:
            m._fx1006_dict("_fair_fresh_noshot")[A] = ws + rel + noshot_rel
        return m
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", FF: "1"}):
        check("partB_never_dispatched_is_contender", _fair(mk(), B) == 2)
        check("partB_dispatched_in_window_not_contender", _fair(mk(rel=0.2), B) == 3)
        check("partB_dispatched_long_after_open_not_contender", _fair(mk(rel=16.0), B) == 3)
        check("partB_within_half_second_before_open_not_contender", _fair(mk(rel=-0.3), B) == 3)
        check("partB_earlier_window_dispatch_is_contender", _fair(mk(rel=-0.7), B) == 2)
        check("partB_long_ago_dispatch_is_contender", _fair(mk(rel=-60.0), B) == 2)
        check("partB_row_ready_with_hint_still_excluded",
              _fair(mk(rel=0.2, row={"status": "ready", "rearm_hint_ts": time.time()}), B) == 3)
        # a race whose accounts all sat out left no shot: still owed its first volley
        check("partB_noshot_same_dispatch_is_contender", _fair(mk(rel=0.2, noshot_rel=-0.05), B) == 2)
        check("partB_noshot_of_an_earlier_race_does_not_count", _fair(mk(rel=0.2, noshot_rel=-5.0), B) == 3)
        check("partB_garbage_stamps_never_raise_and_stay_contender",
              all(_fair(_garbage_dispatch(v), B) == 2 for v in (None, 0, -5.0, float("nan"), "x", True)))
        # a row that is not 'ready' was never a contender (unchanged)
        check("partB_failed_row_unchanged", _fair(mk(rel=-60.0, row={"status": "failed"}), B) == 3)
    with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1"}):
        # flag off: today's rule -- the raced wave counts
        check("partB_flag_off_raced_wave_still_contender", _fair(mk(rel=0.2), B) == 2)


def _garbage_dispatch(v):
    st = {A: _st(win_ago=17.0), B: _st(win_ago=0.5)}
    m = _mgr(statuses=st)
    m._fx1006_dict("_fx1006_last_dispatch")[A] = v
    return m


def _race_agg(n):
    return {"lock": threading.Lock(), "total": n, "started": 0, "finished": 0, "results": {},
            "units": 0, "t0": time.time()}


def test_fair_fresh_race_with_no_shot_keeps_the_first_volley_owed():
    """Through the real _record_race_result: a race whose every account sat out
    (or never got a session) is noted; any real attempt, or a purchase, is not."""
    sit = {"success": False, "reason": "account_parked_hot"}
    held = {"success": False, "reason": "held_cart_other_tcin"}
    nosession = {"success": False, "reason": "session_init_timeout"}
    real = {"success": False, "reason": "rate_limited_429"}
    won = {"success": True, "reason": None, "order_id": "123"}
    cases = (("1", [sit, sit, sit], True), ("1", [sit, held, nosession], True),
             ("1", [sit, real, sit], False), ("1", [sit, won, sit], False), ("1", [real] * 3, False),
             (None, [sit, sit, sit], False))
    for n, (flag, results, want) in enumerate(cases):
        with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", FF: flag}):
            st = {A: _st(win_ago=17.0), B: _st(win_ago=0.5)}
            m = _mgr(states={A: {"status": "attempting", "started_at": time.time()}}, statuses=st)
            agg = _race_agg(len(results))
            m._fx1006_dict("_fx1006_last_dispatch")[A] = agg["t0"] + 0.01   # the dispatch stamp
            for i, r in enumerate(results[:-1]):
                quiet(m._record_race_result, A, dict(r), agg, f"W{i + 1}/x")
            check(f"noshot_{n}_nothing_noted_before_the_last_account",
                  A not in (getattr(m, "_fair_fresh_noshot", None) or {}))
            quiet(m._record_race_result, A, dict(results[-1]), agg, f"W{len(results)}/x")
            noted = A in (getattr(m, "_fair_fresh_noshot", None) or {})
            check(f"noshot_{n}_{flag}_noted_{want}", noted is want, results)
            if flag:
                m._states[A] = {"status": "ready", "rearm_hint_ts": time.time()}   # the emergency reset
                want_cap = 2 if want else 3
                check(f"noshot_{n}_cap_for_B_is_{want_cap}", _fair(m, B) == want_cap, _fair(m, B))


def test_fair_fresh_dispatch_stamp_without_miss_rearm():
    """Part B reads _fx1006_last_dispatch, which only the MISS flag wrote: with the
    new flag alone the dispatch is stamped too; with both off it is not."""
    ev = {B: {"in_stock": True, "title": "B"}}
    for flag, miss, want in (("1", None, True), (None, None, False), (None, "1", True), ("1", "1", True)):
        with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", "TARGET_MULTI_SKU_MISS_REARM": miss, FF: flag}):
            m = _mgr(statuses={B: _st()})
            quiet(m.process_stock_data, ev)
            stamped = B in (getattr(m, "_fx1006_last_dispatch", None) or {})
            check(f"dispatch_stamp_{flag}_{miss}_{want}", m.started == [B] and stamped is want, (m.started, stamped))


def test_fair_fresh_defer_print_has_its_own_log_kind():
    """Part C: the FAIR defer print used to share ('miss', window) with the MISS prints
    -- silent for 30 s after one (0 lines on 10-09). Own kind with the flag, and
    rate-limited under it; flag off keeps the shared kind."""
    ev = {O_: {"in_stock": True, "title": "O"}}
    for flag in ("1", None):
        with env(**{**FLAGS, **MSKU, "TARGET_MULTI_SKU_FAIR_SHARE": "1", "TARGET_MULTI_SKU_MISS_REARM": "1", FF: flag}):
            m = _mgr(states={O_: {"status": "ready"}, F_: {"status": "ready"}},
                     statuses={O_: _st(win_ago=90.0, lf_ago=1.0), F_: _st(win_ago=0.3)})
            check("kind_setup_miss_slot_taken", m._fx1006_log_ok("miss", m._fx1006_window_key(O_)) is True)
            _, o1 = quiet(m.process_stock_data, ev)
            _, o2 = quiet(m.process_stock_data, ev)
            n1, n2 = o1.count("old window -- deferring to fresh"), o2.count("old window -- deferring to fresh")
            if flag:
                check("kind_on_defer_printed_after_a_miss", n1 == 1, o1[-600:])
                check("kind_on_defer_rate_limited_under_its_own_kind", n2 == 0, o2[-600:])
                check("kind_on_text_unchanged", "[MULTI_SKU_FAIR_SHARE]" in o1 and "re-offered next tick" in o1)
            else:
                check("kind_off_defer_hidden_behind_the_miss_slot", n1 == 0 and n2 == 0, (o1[-600:], o2[-600:]))
            check("kind_deferred_either_way_hinted", bool(_rows(m)[O_].get("rearm_hint_ts")), _rows(m)[O_])
            check("kind_deferred_either_way_not_started", m.started == [], m.started)


def test_fair_fresh_real_manager_constructs_with_flag_on():
    """Startup-path rule: the REAL __init__ (state store, latch restore, banners) runs
    with the flag on and builds a manager that handles a flip -- only the session /
    browser system is stubbed out, and the cwd is a temp dir so 'logs/' lands there."""
    import shutil
    tmp = tempfile.mkdtemp(prefix="fairfresh_")
    cwd = os.getcwd()
    real_init_sessions = BulletproofPurchaseManager._initialize_session_system
    m = None
    try:
        os.chdir(tmp)
        os.makedirs("config", exist_ok=True)
        with open(os.path.join("config", "product_config.json"), "w", encoding="utf-8") as f:
            f.write('{"products": []}')     # process_stock_data reads its priority list from cwd
        BulletproofPurchaseManager._initialize_session_system = lambda self: None
        with env(**{**FLAGS, **W9, FF: "1"}):
            m, out = quiet(BulletproofPurchaseManager)
            check("real_init_constructed", isinstance(m, BulletproofPurchaseManager), out[-300:])
            m.worker_pool = _Pool(_fleet())
            m._maybe_run_session_sentinel = lambda: None
            m._warmup_cycle_counter = 5
            started = []
            m.start_purchase = lambda tcin, title, max_qty=1: (started.append(tcin) or {"success": True, "duration": 1})
            b = _st(win_ago=0.75)
            _with_checker(m, {A: _st(win_ago=17.0, lf_ago=17.1), B: b})
            m._fx1006_dict("_fx1006_last_dispatch")[A] = time.time() - 17.0
            m.state_store.set(A, {"status": "ready", "rearm_hint_ts": time.time() - 0.1})
            m.state_store.set(B, {"status": "ready", "rearm_hint_ts": time.time() - 0.7})
            quiet(m.reset_completed_purchases_by_stock_status, EV_W9_B)
            _flicker(b)
            quiet(m.process_stock_data, EV_W9_B)
            check("real_init_flickered_flip_dispatched", started == [B], started)
            check("real_init_stamp_taken", "pre_false" in m.stock_snapshot(B))
    finally:
        try:
            if m is not None:
                m.shutdown()
        except Exception:
            pass
        BulletproofPurchaseManager._initialize_session_system = real_init_sessions
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_fair_fresh_source_pins():
    src = (ROOT / "src/purchasing/bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    mon = (ROOT / "src/monitoring/stock_check_resilient.py").read_text(encoding="utf-8")
    i = src.index("def __init__(self, status_callback")
    j = src.index("def _cleanup_stale_states", i)
    check("pin_init_untouched", "fair_fresh" not in src[i:j] and "FAIR_FRESH" not in src[i:j])
    check("pin_monitor_untouched", "pre_false" not in mon and "window_pre_false_at" not in mon)
    check("pin_flag_exact_one", "os.environ.get('TARGET_FAIR_FRESH_FIX', '0').strip() == '1'" in src)
    k = src.index("def _fx1006_fresh_snap")
    seg = src[k:src.index("def _valid_started_at", k)]
    check("pin_fresh_snap_flag_gated", "if _fair_fresh_on():" in seg and "snap.get('pre_false')" in seg, seg)
    check("pin_note_dispatch_gate",
          "if _miss_rearm_on() or _fair_fresh_on():\n                                self._fx1006_note_dispatch(tcin)" in src)
    check("pin_defer_kind_switch", "'fair_defer' if _fair_fresh_on() else 'miss'" in src)
    check("pin_contenders_gate", "if _fair_fresh_on() and self._fair_fresh_raced(t, snap):" in src)
    check("pin_snapshot_stamp_gate_and_guard",
          "if _fair_fresh_on():\n                try:\n                    self._fair_fresh_stamp(tcin, snap)" in src)
    check("pin_prime_in_both_callbacks", src.count("self._fair_fresh_prime(stock_data)") == 2)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    order = [test_flags_default_off, test_reads_in_stock_pure, test_fair_share_limits,
             test_fair_share_e2e_same_sweep, test_fair_share_e2e_sit_out_order_and_miss,
             test_fair_share_source_pin,
             test_miss_flag_off_bare_ready, test_miss_flag_on_hints_and_restamps, test_miss_no_stamp_cases,
             test_miss_hint_feeds_level_rearm_and_free_worker_dispatches,
             test_miss_rearm_e2e_freed_worker_takes_B, test_single_row_write_no_clobber,
             test_emergency_reset_single_row, test_lru_order_for_multi_tcin_events, test_keep_live_failed,
             test_keep_defensive_reset, test_yield_target_rules, test_yield_sleep,
             test_yield_in_real_race_loop, test_yield_never_on_checkout_level_result,
             test_fair_share_e2e_assignment_spends_the_sitter_first,
             test_fair_share_e2e_sitter_never_reserved_beside_firing,
             test_all_free_sit_out_idle_fleet_is_a_miss, test_purchase_recorded_on_every_path,
             test_keep_skips_bought_this_window, test_miss_log_throttled,
             test_contender_scoring_is_silent, test_held_cart_past_ttl_is_not_a_sit_out,
             test_yield_single_pending_frees_every_waiting_racer,
             test_stale_failed_rows_do_not_cancel_fair_share, test_fresh_window_first_in_multi_tcin_events,
             test_yield_first_shots_only_no_ping_pong, test_acct_dispatch_recorded_on_reservation,
             test_single_ready_worker_sit_out_is_a_miss, test_miss_throttle_is_per_window,
             test_ac1_latch_is_per_identity_and_tcin_stays_offered,
             test_phantom_contender_only_busy_accounts_could_race,
             test_read_gap_renewal_is_not_a_flip, test_old_window_defers_to_fresh_flip_same_sweep,
             test_no_yield_from_legacy_single_worker_path, test_in_delay_failed_message_not_an_error,
             test_fresh_first_sort_without_fair_share,
             test_stale_read_all_sit_out_stays_offered, test_old_window_keeps_accounts_fresh_cannot_use,
             test_line_holder_goes_to_its_tcin, test_pingpong_guard_survives_read_gap,
             test_all_latched_alert_still_printed,
             test_latched_line_holder_is_not_held_for_its_line,
             test_hold_back_only_for_a_contender_a_leftover_can_fire,
             test_two_line_holders_for_the_first_tcin_both_go_there,
             test_yield_flag_off_source_pin, test_lock_order_source_pin,
             test_fair_fresh_flag_default_off, test_fair_fresh_w9_replay_flag_off_is_old_behaviour,
             test_fair_fresh_w9_replay_flag_on, test_fair_fresh_e2e_tick_first_volley_width,
             test_fair_fresh_unraced_contender_splits_with_flickered_flip,
             test_fair_fresh_pre_stamp_rules, test_fair_fresh_snapshot_without_stamp_falls_back,
             test_fair_fresh_callbacks_take_first_sight, test_fair_fresh_part_b_contender_rules,
             test_fair_fresh_race_with_no_shot_keeps_the_first_volley_owed,
             test_fair_fresh_dispatch_stamp_without_miss_rearm,
             test_fair_fresh_defer_print_has_its_own_log_kind,
             test_fair_fresh_real_manager_constructs_with_flag_on, test_fair_fresh_source_pins]
    assert set(order) == set(tests), "every test must be listed in main()"
    for t in order:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Offline tests for FX-1014-REBUY (TARGET_REBUY_NONBUYERS=1, needs
TARGET_MULTI_SKU_MISS_REARM=1; docs/CLAIMS.md C-1014-14): a TCIN one of our
accounts bought stays on offer to the OTHER accounts while it reads in stock;
the buyer sits it out ('bought_this_window') for the rest of that stock window.
Flag off = the exact prior code (a 'purchased' row is never re-armed).

Stub workers / stub checker only: no browser, no network.
Run: python tests/test_rebuy_nonbuyers_1014.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402
import test_mass_restock_1006 as M  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet

A, B = M.A, M.B
_TMP = tempfile.mkdtemp(prefix="rebuy_1014_")
FLAGS = {**M.FLAGS, "TARGET_REBUY_NONBUYERS": None, "TARGET_REBUY_FILE": None,
         "TARGET_REBUY_BUYER_HOLD_S": None, "TARGET_RACE_JOIN_FREE": None}
ON = {**FLAGS, **M.MSKU, "TARGET_REBUY_NONBUYERS": "1", "TARGET_MULTI_SKU_MISS_REARM": "1",
      "TARGET_MULTI_SKU_FAIR_SHARE": "1"}
BOUGHT = {"success": True, "order_number": "X1", "quantity": 1}


def _file():
    return os.path.join(tempfile.mkdtemp(dir=_TMP), "rebuy_buyers.json")


def _bought(m, tcin, label):
    """Record a purchase through the real result path (single-row branch)."""
    bpm_mod._REBUY_TLS.label = ""
    rows = M._rows(m)
    rows_set = getattr(m, "_states", None)
    if rows_set is not None:
        m._states[tcin] = {"status": "attempting", "started_at": time.time()}
    try:
        quiet(m._update_purchase_result, tcin, dict(BOUGHT), worker_label=label)
    except Exception:
        pass
    return rows


def _build():
    from app import StockMonitorThread
    return StockMonitorThread._build_level_rearm_map


# ── 0. flags ─────────────────────────────────────────────────────────────────

def test_flag_default_off_and_needs_miss():
    with env(**FLAGS):
        check("off_by_default", bpm_mod._rebuy_on() is False)
    with env(**{**FLAGS, "TARGET_REBUY_NONBUYERS": "1"}):
        check("needs_miss_rearm", bpm_mod._rebuy_on() is False)
    with env(**{**FLAGS, "TARGET_REBUY_NONBUYERS": "1", "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        check("on_with_miss", bpm_mod._rebuy_on() is True)


def test_flag_off_is_head():
    """Flag off: the buyer is not recorded, nobody sits out on its account, the
    TCIN stays 'recently purchased' and the level re-arm never offers it."""
    with env(**{**FLAGS, "TARGET_MULTI_SKU_MISS_REARM": "1"}):
        m = M._mgr(statuses={A: M._st()})
        _bought(m, A, "W1/primary")
        check("off_no_buyer_record", not getattr(m, "_rebuy_buyers", None), getattr(m, "_rebuy_buyers", None))
        check("off_tcin_blocked_HEAD", m._fx1006_recently_purchased(A) is True)
        check("off_no_skip", m._thread_skip_reason("W1/primary", "primary", A) == "")
        check("off_not_open", m._rebuy_tcin_open(A) is False)
        check("off_rebuy_active_false", m.rebuy_active() is False)
        rows = {A: {"status": "purchased"}}
        check("off_map_excludes_purchased",
              _build()({A: m.stock_monitor._resilient_checker._tcin_status[A]}, rows, time.time()) == {})


# ── 1. per-account record ────────────────────────────────────────────────────

def test_buyer_sits_out_others_fire():
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        _, out = quiet(m._rebuy_note, A, "W1/primary")
        check("note_logged", "[REBUY]" in out and "W1/primary" in out, out)
        check("buyer_skip", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        check("buyer_skip_by_account_id_other_label",
              m._thread_skip_reason("W7/primary", "primary", A) == "bought_this_window")
        check("other_accounts_fire", m._thread_skip_reason("W2/business", "business", A) == ""
              and m._thread_skip_reason("W3/alt-1", "alt-1", A) == "")
        check("buyer_free_on_other_tcin", m._thread_skip_reason("W1/primary", "primary", B) == "")
        check("tcin_open", m._rebuy_tcin_open(A) is True)
        m._fx1006_note_purchase(A)
        check("not_recently_purchased_when_open", m._fx1006_recently_purchased(A) is False)
        w1, w2 = m.worker_pool._w[0], m.worker_pool._w[1]
        check("sits_out_helper_buyer", m._fx1006_worker_sits_out(w1, A) is True)
        check("sits_out_helper_other", m._fx1006_worker_sits_out(w2, A) is False)


def test_result_path_records_label():
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        _bought(m, A, "W2/business")
        check("recorded_from_result", ("W2/business", A) in (getattr(m, "_rebuy_buyers", None) or {}),
              getattr(m, "_rebuy_buyers", None))
        check("result_tcin_open", m._rebuy_tcin_open(A) is True)
        check("result_buyer_sits", m._thread_skip_reason("W2/business", "business", A) == "bought_this_window")
        m2 = M._mgr(statuses={A: M._st(win_ago=30.0)})
        try:
            quiet(m2._update_purchase_result, A, {"success": False, "reason": "rate_limited_429"},
                  worker_label="W2/business")
        except Exception:
            pass
        check("failure_not_recorded", not getattr(m2, "_rebuy_buyers", None))
        m3 = M._mgr(statuses={A: M._st(win_ago=30.0)})
        try:
            quiet(m3._update_purchase_result, A, {"success": False, "ambiguous_commit": True, "reason": "x"},
                  worker_label="W2/business")
        except Exception:
            pass
        check("ambiguous_not_a_buyer_record", not getattr(m3, "_rebuy_buyers", None))


def test_unlabelled_purchase_keeps_head_block():
    """Single-worker path (no worker label): the TCIN keeps HEAD's whole-fleet
    block for its window; nobody gets an extra in-thread skip."""
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        _bought(m, A, None)
        m._fx1006_note_purchase(A)
        check("unlabelled_not_open", m._rebuy_tcin_open(A) is False)
        check("unlabelled_recently_purchased", m._fx1006_recently_purchased(A) is True)
        check("unlabelled_fails_closed_everyone_sits",
              m._thread_skip_reason("W2/business", "business", A) == "bought_this_window"
              and m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        rows = {A: {"status": "purchased"}}
        rearm = _build()({A: m.stock_monitor._resilient_checker._tcin_status[A]}, rows, time.time(),
                         rebuy_open=m._rebuy_rearm_ok)
        check("unlabelled_map_excludes", rearm == {}, rearm)


def test_no_record_purchased_row_not_open():
    """A 'purchased' row with no buyer on record (bought with the flag off, or
    by an earlier process with no file) is never opened."""
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        check("no_record_not_open", m._rebuy_tcin_open(A) is False)
        rows = {A: {"status": "purchased"}}
        rearm = _build()({A: m.stock_monitor._resilient_checker._tcin_status[A]}, rows, time.time(),
                         rebuy_open=m._rebuy_rearm_ok)
        check("no_record_map_excludes", rearm == {}, rearm)


# ── 2. the stock window ──────────────────────────────────────────────────────

def test_window_rules():
    old = bpm_mod._REBUY_PROC_START
    bpm_mod._REBUY_PROC_START = time.time() - 86400.0   # records below are THIS process's
    try:
        _window_rules()
    finally:
        bpm_mod._REBUY_PROC_START = old


def _window_rules():
    with env(**ON):
        # bought 120 s ago; the TCIN read out of stock after that and a new window
        # opened 0.5 s ago (a real out->in flip): the buyer is back, as HEAD.
        m = M._mgr(statuses={A: M._st(win_ago=0.5)})
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
        check("new_window_buyer_back", m._thread_skip_reason("W1/primary", "primary", A) == "")
        # same window (opened 300 s ago, before the purchase): binds
        m = M._mgr(statuses={A: M._st(win_ago=300.0)})
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
        check("same_window_binds", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        # read-gap renewal (window_start after the purchase, but no OOS read since): binds
        m = M._mgr(statuses={A: M._st(win_ago=5.0, lf_ago=400.0)})
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
        check("gap_renewal_binds", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        # verifier (d): read-gap renewal AFTER the purchase, then a short flicker (no
        # renewal): an out-of-stock read after the purchase but no new window after it
        m = M._mgr(statuses={A: M._st(win_ago=60.0, lf_ago=10.0)})
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
        check("gap_then_flicker_binds", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        # flicker inside the purchase's window (window opened before the purchase)
        m = M._mgr(statuses={A: M._st(win_ago=300.0, lf_ago=10.0)})
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
        check("flicker_binds", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        # no stock read at all: the hold decides
        m = M._mgr(statuses={})
        m.stock_monitor = None
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 60.0
        check("no_read_inside_hold_binds",
              m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        m._rebuy_state()[0][("W1/primary", A)] = time.time() - 4000.0
        check("no_read_past_hold_free", m._thread_skip_reason("W1/primary", "primary", A) == "")


def test_restored_record_holds_even_on_new_window():
    """Crash relaunch: a record from a previous process holds for the hold time
    even though the window now looks new (the window it belonged to is unknown)."""
    path = _file()
    with env(**{**ON, "TARGET_REBUY_FILE": path}):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        m._rebuy_path = path
        quiet(m._rebuy_note, A, "W3/alt-1")
        data = json.load(open(path, encoding="utf-8"))
        check("file_written", f"W3/alt-1|{A}" in data.get("buyers", {}), data)
        old = bpm_mod._REBUY_PROC_START
        try:
            bpm_mod._REBUY_PROC_START = time.time() + 1.0   # "this" process started after the note
            m2 = M._mgr(statuses={A: M._st(win_ago=0.5)})   # a fresh-looking window
            _, out = quiet(m2._rebuy_restore)
            check("restore_logged", "restored 1 buyer record" in out, out)
            check("restored_buyer_sits_out",
                  m2._thread_skip_reason("W3/alt-1", "alt-1", A) == "bought_this_window")
            check("restored_others_fire", m2._thread_skip_reason("W1/primary", "primary", A) == "")
            check("restored_tcin_open", m2._rebuy_tcin_open(A) is True)
        finally:
            bpm_mod._REBUY_PROC_START = old
    with env(**{**FLAGS, "TARGET_REBUY_FILE": path}):
        m3 = M._mgr(statuses={A: M._st()})
        check("restore_flag_off_noop", quiet(m3._rebuy_restore)[0] == 0)


def test_load_rejects_bad_entries():
    path = _file()
    now = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"buyers": {f"W1/primary|{A}": now - 10, "nolabel": now, f"W2/b|{B}": "x",
                              f"W3/c|{B}": True, f"W4/d|{B}": now - 90000, f"W5/e|{B}": now + 3600}}, f)
    got = bpm_mod._rebuy_load(path, now)
    check("load_keeps_only_valid", set(got) == {("W1/primary", A)}, got)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("load_garbage_empty", bpm_mod._rebuy_load(path, now) == {})
    check("load_missing_empty", bpm_mod._rebuy_load(path + ".nope", now) == {})


# ── 3. the level re-arm and the real dispatch ────────────────────────────────

def test_map_includes_open_purchased_row():
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0), B: M._st(win_ago=30.0)})
        quiet(m._rebuy_note, A, "W1/primary")
        rows = {A: {"status": "purchased"}, B: {"status": "purchased"}}
        snap = m.stock_monitor._resilient_checker._tcin_status
        rearm = _build()(snap, rows, time.time(), rebuy_open=m._rebuy_rearm_ok)
        check("map_open_A_only", set(rearm) == {A}, rearm)
        rearm0 = _build()(snap, rows, time.time())
        check("map_without_callable_HEAD", rearm0 == {}, rearm0)
        stale = {A: M._st(win_ago=30.0, age=200.0)}
        check("map_stale_read_excluded",
              _build()(stale, rows, time.time(), rebuy_open=m._rebuy_rearm_ok) == {})
        oos = {A: M._st(in_stock=False)}
        check("map_oos_excluded", _build()(oos, rows, time.time(), rebuy_open=m._rebuy_rearm_ok) == {})

        def _boom(*_a):
            raise RuntimeError("x")
        check("map_callable_error_excluded", _build()(snap, rows, time.time(), rebuy_open=_boom) == {})


def test_e2e_non_buyers_race_bought_tcin():
    """REAL reservations + racer threads: W1 bought A (row 'purchased', A still in
    stock). Flag on: the level re-arm offers A, and only W2/W3 fire it. Flag off:
    A is never offered and nobody fires."""
    for flag in ("1", None):
        with env(**{**ON, **M.E2E_ENV, "TARGET_REBUY_NONBUYERS": flag, "TARGET_REBUY_FILE": _file()}):
            gate = threading.Event()
            m, ws = M._e2e_mgr({A: M._st(win_ago=40.0)}, gate)
            m._states[A] = {"status": "purchased", "completed_at": time.time() - 5,
                            "order_number": "X1"}
            quiet(m._rebuy_note, A, "W1/primary")
            m._fx1006_note_purchase(A)
            try:
                rearm = _build()(m.stock_monitor._resilient_checker._tcin_status, m.get_all_states(),
                                 time.time(), rebuy_open=(m._rebuy_rearm_ok if m.rebuy_active() else None))
                out = ""
                if rearm:
                    _, o1 = quiet(m.reset_completed_purchases_by_stock_status, rearm)
                    _, o2 = quiet(m.process_stock_data, rearm)
                    out = o1 + o2
                time.sleep(0.3)
                shots = M._shots(ws)
            finally:
                M._settle(m, gate, ws)
            if flag:
                check("e2e_on_offered", set(rearm) == {A}, rearm)
                check("e2e_on_buyer_never_fires", shots["W1/primary"] == [], shots)
                check("e2e_on_non_buyers_fire", shots["W2/business"] == [A] and shots["W3/alt-1"] == [A],
                      (shots, out[-900:]))
                check("e2e_on_buyer_not_reserved", "W1/primary" not in out.split("reserved")[-1][:200]
                      if "reserved" in out else True, out[-900:])
            else:
                check("e2e_off_not_offered", rearm == {}, rearm)
                check("e2e_off_nobody_fires", all(v == [] for v in shots.values()), shots)
            _res = getattr(m, "_worker_reservations", {})
            check(f"e2e_{'on' if flag else 'off'}_reservations_released", _res == {}, _res)


def test_e2e_all_bought_no_empty_race():
    """Every account bought A: A is a MISS that stays hinted, never an empty race."""
    with env(**{**ON, **M.E2E_ENV, "TARGET_REBUY_FILE": _file()}):
        gate = threading.Event()
        m, ws = M._e2e_mgr({A: M._st(win_ago=40.0)}, gate)
        m._states[A] = {"status": "purchased", "completed_at": time.time() - 5}
        for w in ws:
            quiet(m._rebuy_note, A, w.label())
        m._fx1006_note_purchase(A)
        try:
            rearm = _build()(m.stock_monitor._resilient_checker._tcin_status, m.get_all_states(),
                             time.time(), rebuy_open=m._rebuy_rearm_ok)
            out = ""
            if rearm:
                _, o1 = quiet(m.reset_completed_purchases_by_stock_status, rearm)
                _, o2 = quiet(m.process_stock_data, rearm)
                out = o1 + o2
            time.sleep(0.3)
            shots = M._shots(ws)
        finally:
            M._settle(m, gate, ws)
        check("all_bought_closed_not_offered", rearm == {}, rearm)
        check("all_bought_nobody_fires", all(v == [] for v in shots.values()), shots)
        check("all_bought_no_race", "[RACE]" not in out, out[-900:])
        check("all_bought_tcin_closed", m._rebuy_tcin_open(A) is False)
        check("all_bought_recently_purchased_HEAD", m._fx1006_recently_purchased(A) is True)


def test_in_flight_race_buyer_stops_others_continue():
    """Inside one race: the buyer's thread ends on its success (the loop breaks on
    success) and a sibling's next attempt is not skipped."""
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        quiet(m._rebuy_note, A, "W1/primary")
        check("sibling_next_attempt_fires", m._thread_skip_reason("W2/business", "business", A) == "")
        check("buyer_next_attempt_stops", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")


def test_single_worker_thread_identity_names_the_buyer():
    """Verifier (a): a single-worker purchase has no race label; the purchase
    thread's own identity (the in-thread skip's key) names the buyer."""
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        bpm_mod._REBUY_TLS.label = "W2/business"
        try:
            m._states[A] = {"status": "attempting", "started_at": time.time()}
            try:
                quiet(m._update_purchase_result, A, dict(BOUGHT))
            except Exception:
                pass
        finally:
            bpm_mod._REBUY_TLS.label = ""
        recs = getattr(m, "_rebuy_buyers", {}) or {}
        check("tls_label_recorded", ("W2/business", A) in recs, recs)
        check("tls_buyer_sits", m._thread_skip_reason("W2/business", "business", A) == "bought_this_window")
        check("tls_others_fire", m._thread_skip_reason("W1/primary", "primary", A) == "")
        src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
        check("tls_set_from_thread_worker", "_REBUY_TLS.label = self._ac_ident(worker)" in src)


def test_ambiguous_commit_counts_as_buyer_for_the_window():
    """Verifier (b): an AC-1 latch also writes a buyer record, so the latched
    account sits the bought TCIN out for the whole window, not just 1800 s."""
    with env(**{**ON, "TARGET_AMBIGUOUS_COMMIT_LATCH": "1"}):
        m = M._mgr(statuses={A: M._st(win_ago=4000.0)})
        quiet(m._ac_latch_mark, "W2/business", A, "po_2xx_ambiguous")
        check("ac1_writes_buyer_record", ("W2/business", A) in (getattr(m, "_rebuy_buyers", {}) or {}))
        m._ac_latch.clear()                     # the 1800 s latch has expired
        m._rebuy_state()[0][("W2/business", A)] = time.time() - 2000.0
        check("ac1_expired_still_sits_out_window",
              m._thread_skip_reason("W2/business", "business", A) == "bought_this_window")
    with env(**{**FLAGS, "TARGET_AMBIGUOUS_COMMIT_LATCH": "1"}):
        m = M._mgr(statuses={A: M._st()})
        quiet(m._ac_latch_mark, "W2/business", A, "x")
        check("ac1_flag_off_no_record", not getattr(m, "_rebuy_buyers", None))


def test_fails_closed():
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        quiet(m._rebuy_note, A, "W1/primary")

        def _boom(*a, **k):
            raise RuntimeError("snapshot down")
        m.stock_snapshot = _boom
        check("snapshot_error_binds_buyer", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
        check("snapshot_error_other_tcin_free", m._thread_skip_reason("W1/primary", "primary", B) == "")
        check("sit_out_reason_registered", bpm_mod._is_sit_out_reason("bought_this_window"))


def test_closed_when_every_ready_account_bought():
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        quiet(m._rebuy_note, A, "W1/primary")
        quiet(m._rebuy_note, A, "W2/business")
        check("two_of_three_open", m._rebuy_tcin_open(A) is True)
        quiet(m._rebuy_note, A, "W3/alt-1")
        check("three_of_three_closed", m._rebuy_tcin_open(A) is False)
        m2 = M._mgr(statuses={A: M._st(win_ago=30.0)})
        quiet(m2._rebuy_note, A, "W1/primary")
        m2.worker_pool._w = [m2.worker_pool._w[0]]          # only the buyer is ready
        check("only_buyer_ready_closed", m2._rebuy_tcin_open(A) is False)
        m2.worker_pool._w = []
        check("no_ready_closed", m2._rebuy_tcin_open(A) is False)


def test_purchased_row_kept_through_other_events():
    """Liveness: another TCIN's event must not reset an open 'purchased' row to a
    bare 'ready' the level re-arm never re-offers; nor the 30 s stuck reset."""
    for flag in ("1", None):
        with env(**{**ON, "TARGET_REBUY_NONBUYERS": flag, "TARGET_STOCK_RESET_KEEP_LIVE_FAILED": "1"}):
            m = M._mgr(states={A: {"status": "purchased", "completed_at": time.time() - 60},
                               B: {"status": "ready"}},
                       statuses={A: M._st(win_ago=90.0), B: M._st()})
            if flag:
                quiet(m._rebuy_note, A, "W1/primary")
            m._fx1006_note_purchase(A)
            quiet(M._reset, m, {B: {"in_stock": True}})
            st = M._rows(m)[A]["status"]
            if flag:
                check("keep_purchased_absent_from_event", st == "purchased", M._rows(m)[A])
                m._states[A] = {"status": "purchased", "completed_at": time.time() - 60}
                quiet(m.process_stock_data, {B: {"in_stock": True, "title": "B"}})
                check("keep_purchased_30s_stuck", M._rows(m)[A]["status"] == "purchased", M._rows(m)[A])
                quiet(M._reset, m, {A: {"in_stock": False}})
                check("oos_in_event_resets", M._rows(m)[A]["status"] == "ready", M._rows(m)[A])
            else:
                check("off_purchased_reset_HEAD", st == "ready", M._rows(m)[A])


def test_round2_bare_ready_row_reoffered_in_bought_window():
    """Round-2 S2: a row reset to bare 'ready' while the only non-buyer was briefly
    not ready must be re-offered once it is ready again (bought window still live)."""
    with env(**ON):
        m = M._mgr(statuses={A: M._st(win_ago=300.0)})
        quiet(m._rebuy_note, A, "W1/primary")
        quiet(m._rebuy_note, A, "W2/business")
        rows = {A: {"status": "ready"}}
        snap = m.stock_monitor._resilient_checker._tcin_status
        rearm = _build()(snap, rows, time.time(), rebuy_open=m._rebuy_rearm_ok)
        check("s2_ready_reoffered_nonbuyer_ready", set(rearm) == {A}, rearm)
        full = list(m.worker_pool._w)
        m.worker_pool._w = full[:2]                      # alt-1 (the non-buyer) not ready
        rearm = _build()(snap, rows, time.time(), rebuy_open=m._rebuy_rearm_ok)
        check("s2_not_offered_while_nonbuyer_down", rearm == {}, rearm)
        m.worker_pool._w = full
        rearm = _build()(snap, rows, time.time(), rebuy_open=m._rebuy_rearm_ok)
        check("s2_offered_again_when_back", set(rearm) == {A}, rearm)
        check("s2_rearm_ok_rejects_failed_status", m._rebuy_rearm_ok(A, "failed") is False)
    old = bpm_mod._REBUY_PROC_START
    bpm_mod._REBUY_PROC_START = time.time() - 86400.0
    try:
        with env(**ON):
            # a NEW window (out-of-stock read after the purchase, then a new window):
            # not ours to re-offer -- the edge publisher's job (HEAD)
            m = M._mgr(statuses={A: M._st(win_ago=0.5)})
            m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
            rows = {A: {"status": "ready"}}
            rearm = _build()(m.stock_monitor._resilient_checker._tcin_status, rows, time.time(),
                             rebuy_open=m._rebuy_rearm_ok)
            check("s2_new_window_not_rearmed", rearm == {}, rearm)
            check("s2_window_not_live", m._rebuy_window_live(A) is False)
    finally:
        bpm_mod._REBUY_PROC_START = old
    with env(**FLAGS):
        m = M._mgr(statuses={A: M._st(win_ago=300.0)})
        rows = {A: {"status": "ready"}}
        check("s2_flag_off_bare_ready_HEAD",
              _build()(m.stock_monitor._resilient_checker._tcin_status, rows, time.time()) == {})


class _S4Pool:
    """Round-2 S4: the sticky worker (and primary) is the buyer; only the
    non-buyer business is ready."""
    def __init__(self, buyer, ready):
        self._buyer = buyer
        self._ready = list(ready)

    def ready_workers(self):
        return list(self._ready)

    @property
    def primary(self):
        return self._buyer

    @property
    def workers(self):
        return [self._buyer] + list(self._ready)

    def acquire_for_tcin(self, tcin):
        return self._buyer


def test_round2_single_worker_path_dispatches_the_nonbuyer():
    for flag in ("1", None):
        with env(**{**ON, **M.E2E_ENV, "TARGET_REBUY_NONBUYERS": flag, "TARGET_REBUY_FILE": _file()}):
            gate = threading.Event()
            m, ws = M._e2e_mgr({A: M._st(win_ago=40.0)}, gate)
            m.worker_pool = _S4Pool(ws[0], [ws[1]])
            m.worker = ws[0]
            m._states[A] = {"status": "purchased", "completed_at": time.time() - 5}
            quiet(m._rebuy_note, A, "W1/primary")
            m._fx1006_note_purchase(A)
            try:
                out = ""
                if flag:
                    rearm = _build()(m.stock_monitor._resilient_checker._tcin_status, m.get_all_states(),
                                     time.time(), rebuy_open=m._rebuy_rearm_ok)
                    check("s4_offered", set(rearm) == {A}, rearm)
                    _, o1 = quiet(m.reset_completed_purchases_by_stock_status, rearm)
                    _, o2 = quiet(m.process_stock_data, rearm)
                    out = o1 + o2
                time.sleep(0.4)
                shots = M._shots(ws)
            finally:
                M._settle(m, gate, ws)
            if flag:
                check("s4_buyer_never_fires", shots["W1/primary"] == [], shots)
                check("s4_nonbuyer_fires", shots["W2/business"] == [A], (shots, out[-900:]))
                check("s4_swap_logged", "dispatching W2/business instead" in out, out[-900:])
            else:
                check("s4_off_nobody_fires", all(v == [] for v in shots.values()), shots)


def test_round2_save_snapshot_inside_lock_and_unreadable_file():
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    i = src.index("    def _rebuy_note(")
    body = src[i:i + 2500]
    check("s1_snapshot_inside_save_lock",
          body.index("with _REBUY_SAVE_LOCK:") < body.index("snap = dict(d)") < body.index("_rebuy_save(path, snap, now)"))
    path = _file()
    with env(**{**ON, "TARGET_REBUY_FILE": path}):
        m = M._mgr(statuses={A: M._st(win_ago=30.0)})
        m._rebuy_path = path
        ths = [threading.Thread(target=m._rebuy_note, args=(A, f"W{i}/acct{i}")) for i in range(1, 9)]

        def _run_all():
            for t in ths:
                t.start()
            for t in ths:
                t.join()
        quiet(_run_all)
        data = json.load(open(path, encoding="utf-8"))
        check("s1_concurrent_notes_all_saved", len(data.get("buyers", {})) == 8, data)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{garbage")
        m2 = M._mgr(statuses={A: M._st()})
        _, out = quiet(m2._rebuy_restore)
        check("unreadable_file_warned", "buyers file unreadable" in out and "check order history" in out, out)


def test_round3_q1_read_gap_does_not_release_the_buyer():
    """Round-3 Q1: one out-of-stock read after the buy, then a read gap longer than
    the window hysteresis renews window_start with no sell-out -- the buyer stays
    bound. A real flip (new window right after the out-of-stock read) releases."""
    old = bpm_mod._REBUY_PROC_START
    bpm_mod._REBUY_PROC_START = time.time() - 86400.0
    try:
        with env(**ON):
            # one False read 40 s ago, the window renewed 0.5 s ago (ws - lf = 39.5 s)
            m = M._mgr(statuses={A: M._st(win_ago=0.5, lf_ago=40.0)})
            m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
            check("q1_read_gap_binds", m._thread_skip_reason("W1/primary", "primary", A) == "bought_this_window")
            # a real flip: last False read 0.8 s before the new window
            m = M._mgr(statuses={A: M._st(win_ago=0.5, lf_ago=1.3)})
            m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
            check("q1_real_flip_releases", m._thread_skip_reason("W1/primary", "primary", A) == "")
        with env(**{**ON, "TARGET_REBUY_FLIP_GAP_S": "60"}):
            m = M._mgr(statuses={A: M._st(win_ago=0.5, lf_ago=40.0)})
            m._rebuy_state()[0][("W1/primary", A)] = time.time() - 120.0
            check("q1_gap_knob", m._thread_skip_reason("W1/primary", "primary", A) == "")
    finally:
        bpm_mod._REBUY_PROC_START = old


def test_round3_q2_swap_skips_reserved_and_busy():
    """Round-3 Q2: the single-worker swap never takes an account reserved for, or
    racing, another TCIN -- it then keeps the original (sitting-out) worker."""
    with env(**{**ON, **M.E2E_ENV, "TARGET_REBUY_FILE": _file()}):
        gate = threading.Event()
        m, ws = M._e2e_mgr({A: M._st(win_ago=40.0)}, gate)
        m.worker_pool = _S4Pool(ws[0], [ws[1]])
        m.worker = ws[0]
        m._states[A] = {"status": "purchased", "completed_at": time.time() - 5}
        quiet(m._rebuy_note, A, "W1/primary")
        m._fx1006_note_purchase(A)
        m._worker_reservations = {"W2/business": (B, time.time())}      # business is B's
        try:
            rearm = _build()(m.stock_monitor._resilient_checker._tcin_status, m.get_all_states(),
                             time.time(), rebuy_open=m._rebuy_rearm_ok)
            out = ""
            if rearm:
                _, o1 = quiet(m.reset_completed_purchases_by_stock_status, rearm)
                _, o2 = quiet(m.process_stock_data, rearm)
                out = o1 + o2
            time.sleep(0.4)
            shots = M._shots(ws)
        finally:
            M._settle(m, gate, ws)
        check("q2_reserved_not_swapped", "dispatching W2/business instead" not in out, out[-900:])
        check("q2_reserved_never_fires_A", A not in shots["W2/business"], shots)
        check("q2_buyer_never_fires", shots["W1/primary"] == [], shots)


def test_source_pins():
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("pin_note_on_result_path", "self._rebuy_note(tcin, worker_label)" in src)
    check("pin_restore_in_init", "self._rebuy_restore()" in src)
    check("pin_all_latched_counts_rebuy", "or _ident_rest.identity_rest_on() or _rebuy_on()):" in src)
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    check("pin_app_passes_rebuy_open", "rebuy_open=_rb_open" in app)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    check("pin_bat_miss_rearm_armed", "set TARGET_MULTI_SKU_MISS_REARM=1" in bat)


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
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

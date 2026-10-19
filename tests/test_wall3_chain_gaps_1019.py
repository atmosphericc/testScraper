#!/usr/bin/env python3
"""Offline test: wall-3 chain GAPS, under the bat's own manager settings (FS-BP-1019-T1; BUILD_SPEC_1019 lane
bptests). test_wall3_chain_1018.py composed the held-line strike, the won-cart ticket loop, AC-1 / PO_2XX
ambiguity and REBUY under a hand-picked, mostly-OFF manager bundle (WAVE_FIRST_ONLY=0, SHOT_BANK_GATE=0,
401_PULSE=0, DCO_BURST=0, RETRY_STOP_WHEN_OOS=0, FAST_CADENCE unset, retry MAX 6, edge delay 1 s). This file
re-runs five transitions the audit named as unpinned, with the TARGET_* environment READ FROM THE WRAPPER
(run_bot_with_nightly_restart.bat through _bat_env(): DCO_BURST=1, WAVE_FIRST_ONLY=1, SHOT_BANK_GATE=1,
401_PULSE=1, RETRY_STOP_WHEN_OOS=1, FAST_CADENCE_IDENTS=all, FAST_SELLING_COOLDOWN_S=45, _PER_TCIN=1,
RETRY_WHILE_IN_STOCK_MAX=40, edge delay 2.0-3.0 s, the whole WONCART / HELD_CART / RIDE bundle).

What is real: BulletproofPurchaseManager (_start_real_purchase, the race thread, the retry-while-in-stock loop
with its transient-reason set, the AC-1 latch, _record_race_result) and a REAL PurchaseExecutor per account
(execute_purchase -> _execute_purchase_impl -> _apply_fast_lane_result -> woncart_eligible -> the real
_won_cart_ticket_loop -> _woncart_exit -> _held_cart_entry -> _fast_selling_cooling_down -> the legacy ATC
branch). What is a model: Target (carts, FAST_SELLING gate, order endpoint, the cart read) behind the
account's tab; the executor clock is fake (ticket gaps of seconds cost nothing); the manager runs in real time.

Scenarios (each one names the transition it pins):
  A  in-chain place-order 429 FAST_SELLING and 429 RESERVATION_FAILURE both enter the won-cart loop (no second
     ATC; the loop's ticket is the order) -- with TARGET_WONCART_RF_ENTRY=1 as the bat arms it; control: with
     RF_ENTRY=0 the RESERVATION_FAILURE shape does NOT enter the loop (legacy fallthrough, no tickets).
  B  in-chain place-order 424 RESERVATION_FAILURE falls through to the legacy checkout: one ATC, one
     place-order POST, no ticket, no second ATC, no unknown tab call, nothing latched.
  C  the loop reaches budget_spent on a LIVE cart and exits won_cart_held; the racer ends (no re-shot, no
     delete); a level re-arm dispatch (the same _start_real_purchase the re-arm ends in) re-enters through
     _held_cart_entry WITHOUT an add-to-cart and the next ticket buys.
  D  cart_evicted after a FAST_SELLING, then the manager's in-thread retry INSIDE the 45 s per-TCIN cooldown:
     the fast lane (chain) is skipped, the LEGACY add-to-cart path fires once, and the model's order endpoint
     is never reached twice.
  E  a ride-to-OOS tail: the stock goes out of stock mid-ride, exactly TARGET_WONCART_OOS_TAIL_TICKETS (1)
     more ticket fires, the loop exits tail_spent, the line is KEPT (held marker, no delete), the racer ends.

MUTATION CHECK (by hand, 2026-10-19, each reverted; not automated): RF_ENTRY admission returning False fails the
A/rf checks; _woncart_exit never holding (elif False) fails C and E; _fast_selling_cooling_down's cooldown read forced to 0 fails D
(a second chain fires and a double order appears); the OOS tail allowance raised by one (both the loop-top and the
fire-time gate; either alone is masked by the other) fails E.

NOT COVERED (stubbed or out of reach, listed so nobody reads the file as more than it is):
  - the legacy checkout that follows a recovered add (scenarios B, D): the executor is stopped at its
    'checking_out' status (the harness raises LegacyReached there), so "a 201 does not double-order" in D is
    asserted up to that boundary: no chain, no ticket, at most ONE order POST reaches the model;
  - app.py's _build_level_rearm_map (the re-arm tick itself): the re-arm is modelled as the dispatch it ends
    in, a second _start_real_purchase on the 'failed' row;
  - the non-edge retry delay (2.5-3.5 s, unset in the bat) is zeroed for run time; the edge delay stays the
    bat's 2.0-3.0 s (no scenario here draws an edge 429).

Offline: no browser, no network. Run: python tests/test_wall3_chain_gaps_1019.py
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_wall3_chain_1018 as W  # noqa: E402  (the 1018 model + world; its main() does not run)
import test_fast_cadence_1011 as FC  # noqa: E402  (_bat_env only)
import test_identity_rest as H  # noqa: E402
import test_mass_restock_1006 as M  # noqa: E402
import test_won_cart_direct_smoke as S  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402

check = H.check
env = H.env
T = W.T
QTY = W.QTY
_TMP = tempfile.mkdtemp(prefix="wall3_gaps_1019_")
RF_BODY = json.dumps({"errorKey": "RESERVATION_FAILURE", "message": "reservation failure"})
BIG = 10 ** 6


def bat_manager_env(extra=None):
    """The wrapper's TARGET_* settings, plus the two harness-only edits (see the docstring) and
    app.py's own setdefault of API place-order."""
    b = {k: v for k, v in FC._bat_env().items() if k.startswith("TARGET_")}
    e = {**M.FLAGS, **b}
    e["TARGET_API_PLACE_ORDER"] = "true"                 # app.py: os.environ.setdefault(..., 'true')
    e["TARGET_ATC_RETRY_DELAY_MIN"] = "0"                # non-edge retry delay, zeroed for run time
    e["TARGET_ATC_RETRY_DELAY_MAX"] = "0"
    # a single-TCIN world: the multi-SKU reservation would only add a second row to read
    e["TARGET_MULTI_SKU_DISPATCH"] = None
    e.update(extra or {})
    return e


def bat_has(**want):
    b = FC._bat_env()
    return all(b.get(k) == v for k, v in want.items())


# ───────────────────────────── the Target model, widened ──────────────────────────────

class Target2(W.Target):
    """W.Target plus: the place-order plan kinds '429rf' (429 RESERVATION_FAILURE) and '424' (Target
    empties the cart, answers 424 RESERVATION_FAILURE); the legacy add-to-cart; a per-call hook."""

    def __init__(self, qty=QTY):
        super().__init__(qty)
        self.atc_legacy = {}         # ident -> legacy add-to-cart POSTs
        self.on_ticket = None        # fn(model, ident, mode) called under no lock after every ticket
        self.tickets_n = {}

    def _po(self, ident, tcin):
        a = self.a[ident]
        if a["po_plan"] and a["po_plan"][0] in ("429rf", "424", "424keep"):
            kind = a["po_plan"].pop(0)
            key = (ident, tcin)
            self.po_posts[key] = self.po_posts.get(key, 0) + 1
            self._note(ident, f"po_{kind}")
            if kind == "429rf":
                return 429, RF_BODY
            if kind == "424":                            # Target empties the cart (every 424 on record did)
                a["cart"] = [c for c in a["cart"] if c["tcin"] != tcin]
            return 424, RF_BODY
        return super()._po(ident, tcin)

    def ticket(self, ident, mode):
        out = super().ticket(ident, mode)
        self.tickets_n[ident] = self.tickets_n.get(ident, 0) + 1
        if self.on_ticket:
            self.on_ticket(self, ident, mode)
        return out

    def legacy_atc(self, ident, tcin, qty):
        with self.lk:
            a = self.a[ident]
            self.atc_legacy[ident] = self.atc_legacy.get(ident, 0) + 1
            if a["edge"] > 0:
                a["edge"] -= 1
                self._note(ident, "latc_edge")
                return {"status": 429, "body": "", "cart_items": []}
            if self._lines(a, tcin):
                self._note(ident, "latc_400")
                return {"status": 400, "body": W.MAXP_BODY, "cart_items": []}
            self._note(ident, "latc_201")
            a["cart"].append(self.line(tcin, qty, ident))
            return {"status": 201, "body": "", "cart_items": [{"tcin": tcin, "quantity": qty}]}


class Tab2(W.ModelTab):
    """The model tab plus the executor's legacy (non-chain) add-to-cart fetch."""

    async def evaluate(self, js, await_promise=False, **kw):
        if "cart_items?field_groups=CART,CART_ITEMS,SUMMARY" in js and "method: 'POST'" in js \
                and "const MODE = '" not in js:
            q = int(re.search(r"quantity:\s*(\d+)", js).group(1))
            t = re.search(r"tcin:\s*'(\d+)'", js).group(1)
            return copy.deepcopy(self.model.legacy_atc(self.ident, t, q))
        return await super().evaluate(js, await_promise, **kw)


class ClockedExec(S.PurchaseExecutor):
    """The executor with ONE clock. In production the manager stamps `_mgr_submit_ts` with the same
    wall clock the executor reads; here the executor's clock is fake (ticket gaps cost nothing) while the
    manager's is real, so a second dispatch after a 240 s fake ride would see a stale stamp and a 'spent'
    deadline. The setter therefore stamps with the executor's own (fake) clock; the deadline arithmetic
    that reads it is the real code."""

    @property
    def _mgr_submit_ts(self):
        return self.__dict__.get("_mgr_submit_ts_v", 0.0)

    @_mgr_submit_ts.setter
    def _mgr_submit_ts(self, v):
        self.__dict__["_mgr_submit_ts_v"] = float(S.pe_mod.time.time())


class World1(W.World):
    """W.World for a chosen set of accounts, with Tab2 and the bat's manager environment applied by the
    caller (the world itself only builds objects)."""

    def __init__(self, model, idents, rebuy_file, latch_file, statuses=None, probe=None):
        self.idents = tuple(idents)
        self.model = model
        self.clock = S.Clock(time.time())
        saved = W.ModelTab
        W.ModelTab = Tab2
        try:
            self.exs = {i: W.make_exec(i, self.clock, model) for i in self.idents}
        finally:
            W.ModelTab = saved
        for ex in self.exs.values():
            ex.__dict__["_mgr_submit_ts_v"] = ex.__dict__.pop("_mgr_submit_ts", 0.0)
            ex.__class__ = ClockedExec
            ex.held_entry_calls = []
            _real_hce = ex._held_cart_entry

            async def _spy(tab, tcin, quantity, start_time, _r=_real_hce, _ex=ex):
                res = await _r(tab, tcin, quantity, start_time)
                _ex.held_entry_calls.append((str(tcin), (res or {}).get("reason") if isinstance(res, dict) else None))
                return res

            ex._held_cart_entry = _spy                 # a pass-through spy: the real method still runs
            # the harness stub returns a fixed deadline from the executor's creation time; a second
            # dispatch needs the REAL ride (deadline = this purchase's start + TARGET_WON_CART_RIDE_MAX_S)
            del ex._begin_won_cart_ride
        self.ws = [W.Worker(n + 1, i, self.exs[i]) for n, i in enumerate(self.idents)]
        self.statuses = statuses if statuses is not None else {T: M._st(win_ago=40.0)}
        self.rebuy_file, self.latch_file = rebuy_file, latch_file
        self.m = self.new_manager()
        self.fresh = W.Fresh(self.statuses)
        self.fresh.start()
        self.out = io.StringIO()

    def ex(self, ident="primary"):
        return self.exs[ident]


def mk(accts, **kw):
    model = Target2()
    for ident, spec in accts.items():
        model.account(ident, **spec)
    d = tempfile.mkdtemp(dir=_TMP)
    return World1(model, tuple(accts), os.path.join(d, "rebuy_buyers.json"), os.path.join(d, "ac_latch.json"), **kw)


def row(m):
    return (m._load_states_unsafe() or {}).get(T, {})


def unknown(w):
    return {i: w.exs[i].tab.unknown for i in w.idents if w.exs[i].tab.unknown}


# ────────────────────────────────── A: in-chain 429 entries ──────────────────────────────────

def _run_a(kind, extra=None):
    body_kind = {"fs": "429fs", "rf": "429rf"}[kind]
    with env(**bat_manager_env(extra)):
        w = mk({"primary": dict(po_plan=[body_kind])})
        try:
            ok = w.race()
            return w, ok
        finally:
            w.close()


def test_a_in_chain_429_enters_the_loop():
    check("a_bat_arms_what_the_scenario_needs", bat_has(TARGET_WONCART_DIRECT="1", TARGET_WONCART_RF_ENTRY="1",
                                                        TARGET_FAST_CADENCE_IDENTS="all", TARGET_WAVE_FIRST_ONLY="1",
                                                        TARGET_SHOT_BANK_GATE="1", TARGET_ATC_DCO_BURST="1",
                                                        TARGET_RETRY_STOP_WHEN_OOS="1", TARGET_401_PULSE="1",
                                                        TARGET_RETRY_WHILE_IN_STOCK_MAX="40",
                                                        TARGET_FAST_SELLING_COOLDOWN_S="45",
                                                        TARGET_FAST_SELLING_COOLDOWN_PER_TCIN="1",
                                                        TARGET_ATC_EDGE429_RETRY_DELAY_MIN="2.0",
                                                        TARGET_ATC_EDGE429_RETRY_DELAY_MAX="3.0"), str(FC._bat_env().get("TARGET_WONCART_RF_ENTRY")))
    for kind in ("fs", "rf"):
        w, ok = _run_a(kind)
        model, ex = w.model, w.ex()
        log = w.log()
        calls = model.calls["primary"]
        tag = f"a_{kind}"
        check(f"{tag}_race_finished_purchased", ok and row(w.m).get("status") == "purchased", str(row(w.m)))
        check(f"{tag}_chain_then_ticket_then_order", calls == ["atc_201", f"po_429{kind}", "po_200"], str(calls))
        check(f"{tag}_one_chain_no_second_atc", ex.fl_calls == [QTY] and model.n_calls("primary", "atc") == 1
              and model.atc_legacy.get("primary", 0) == 0, f"fl={ex.fl_calls} calls={calls}")
        check(f"{tag}_loop_entered_first", "[WON_CART_DIRECT] start entry=first" in log, log[-1200:])
        check(f"{tag}_one_order_exactly", [o[0] for o in model.orders] == ["primary"]
              and model.po_posts.get(("primary", T)) == 2 and not model.po_empty, f"{model.orders} {model.po_posts}")
        check(f"{tag}_order_number_recorded", row(w.m).get("order_number") == model.orders[0][2]
              if model.orders else False, str(row(w.m)))
        check(f"{tag}_no_unknown_tab_calls", not unknown(w), str(unknown(w)))
        check(f"{tag}_not_the_legacy_checkout", "checking_out" not in ex.statuses, str(ex.statuses))
    # control: the RESERVATION_FAILURE shape needs RF_ENTRY; with it off the loop is not entered.
    w, ok = _run_a("rf", extra={"TARGET_WONCART_RF_ENTRY": "0", "TARGET_RETRY_WHILE_IN_STOCK": "0"})
    model, ex = w.model, w.ex()
    check("a_rf_control_flag_off_no_loop", "[WON_CART_DIRECT] start" not in w.log() and model.tickets_n.get("primary", 0) == 0
          and ex.fl_calls == [QTY], f"tickets={model.tickets_n} fl={ex.fl_calls}")
    check("a_rf_control_flag_off_legacy_fallthrough", "checking_out" in ex.statuses and not model.orders,
          f"{ex.statuses} {model.orders}")


# ───────────────────────────────── B: in-chain 424 falls through ─────────────────────────────────

def _run_b(plan, extra=None):
    with env(**bat_manager_env(extra)):
        w = mk({"primary": dict(po_plan=[plan])})
        try:
            return w, w.race()
        finally:
            w.close()


def test_b_in_chain_424_falls_through():
    # B1: the usual 424 -- Target emptied the cart. Under the bat (the strike is armed) the legacy
    # checkout's sink guard cannot tell an emptied cart from an unreadable one (has_items False is not a
    # qualifying read), so it refuses to check out and the result is held_cart_release_failed, which the
    # manager does not retry in-thread. PINNED AS TODAY'S BEHAVIOUR (see defects_found in the report).
    w, ok = _run_b("424")
    model, ex, log = w.model, w.ex(), w.log()
    check("b1_race_finished", ok, str(row(w.m)))
    check("b1_calls_are_exactly_atc_then_one_po", model.calls["primary"] == ["atc_201", "po_424"],
          str(model.calls["primary"]))
    check("b1_no_second_atc", ex.fl_calls == [QTY] and model.n_calls("primary", "atc") == 1
          and model.atc_legacy.get("primary", 0) == 0, f"{ex.fl_calls} {model.atc_legacy}")
    check("b1_no_ticket_no_loop", model.tickets_n.get("primary", 0) == 0 and ex.loop_calls == []
          and "[WON_CART_DIRECT] start" not in log, f"{model.tickets_n} {log[-800:]}")
    check("b1_fell_through_not_into_a_checkout", "place-order rejected: HTTP 424" in log
          and "[LEGACY_CART_GUARD]" in log and "checking_out" not in ex.statuses, f"{ex.statuses} {log[-900:]}")
    check("b1_result_is_held_cart_release_failed_not_retried",
          "Reason 'held_cart_release_failed' not transient" in log and "(attempt 2)" not in log
          and row(w.m).get("status") == "failed", f"{row(w.m)} {log[-700:]}")
    check("b1_one_place_order_post_no_order", model.po_posts.get(("primary", T)) == 1 and not model.orders,
          f"{model.po_posts} {model.orders}")
    check("b1_no_unknown_tab_calls", not unknown(w), str(unknown(w)))
    check("b1_nothing_latched", not getattr(w.m, "_ac_latch", None) and "[AMBIGUOUS_COMMIT]" not in log,
          str(getattr(w.m, "_ac_latch", None)))
    check("b1_the_cart_is_empty_nothing_left_behind", not model.a["primary"]["cart"]
          and getattr(ex, "_woncart_dirty", None) is None and ex._held_cart is None,
          f"{model.a['primary']['cart']} {getattr(ex, '_woncart_dirty', None)} {ex._held_cart}")
    # B1 control: the strike off for the account -> no sink guard; the legacy checkout is reached.
    w, ok = _run_b("424", {"TARGET_HELD_LINE_FLIP_STRIKE": "0"})
    model, ex, log = w.model, w.ex(), w.log()
    check("b1c_strike_off_reaches_the_legacy_checkout", "checking_out" in ex.statuses
          and "[LEGACY_CART_GUARD]" not in log and model.calls["primary"] == ["atc_201", "po_424"],
          f"{ex.statuses} {model.calls['primary']}")
    # B2: a 424 that left our line in the cart: the guard's read shows only our TCIN -> legacy checkout.
    w, ok = _run_b("424keep")
    model, ex, log = w.model, w.ex(), w.log()
    check("b2_calls_are_exactly_atc_then_one_po", model.calls["primary"] == ["atc_201", "po_424keep"],
          str(model.calls["primary"]))
    check("b2_no_second_atc_no_ticket", ex.fl_calls == [QTY] and model.atc_legacy.get("primary", 0) == 0
          and model.tickets_n.get("primary", 0) == 0 and ex.loop_calls == [], f"{ex.fl_calls} {model.tickets_n}")
    check("b2_legacy_checkout_reached_on_our_line_only", "checking_out" in ex.statuses
          and model.po_posts.get(("primary", T)) == 1 and not model.orders, f"{ex.statuses} {model.po_posts}")
    check("b2_no_unknown_tab_calls", not unknown(w), str(unknown(w)))


# ─────────────── C: budget_spent on a live cart -> won_cart_held -> re-arm re-entry ───────────────

def test_c_budget_spent_held_then_rearm_reentry():
    with env(**bat_manager_env()):
        w = mk({"primary": dict(fs=BIG)})
        try:
            ok = w.race()
            model, ex, m = w.model, w.ex(), w.m
            log1 = w.log()
            n1 = model.tickets_n.get("primary", 0)
            check("c_first_race_finished_failed", ok and row(m).get("status") == "failed", str(row(m)))
            check("c_loop_ended_budget_spent_on_a_live_cart", "[WON_CART_DIRECT] end reason=budget_spent" in log1
                  and " live=True " in log1 and "held=yes" in log1, log1[-1500:])
            check("c_many_tickets_one_chain", n1 >= 30 and ex.fl_calls == [QTY] and model.n_calls("primary", "atc") == 1,
                  f"tickets={n1} fl={ex.fl_calls} calls={model.calls['primary'][:3]}")
            check("c_result_won_cart_held_not_retried", "Reason 'won_cart_held' not transient" in log1
                  and "(attempt 2)" not in log1, log1[-900:])
            check("c_racer_ended_nothing_in_flight", not any(i["thread"].is_alive() for i in m._active_purchases.values()),
                  str(m._active_purchases))
            check("c_line_kept_no_delete", ex.deletes == [] and len(model.a["primary"]["cart"]) == 1
                  and model.a["primary"]["cart"][0]["qty"] == QTY, f"{ex.deletes} {model.a['primary']['cart']}")
            h = ex._held_cart
            check("c_held_marker_set", isinstance(h, dict) and h.get("tcin") == T and h.get("tickets") == n1
                  and getattr(ex, "_woncart_dirty", None) is None, str(h))
            check("c_no_order_yet", not model.orders and not model.po_empty, str(model.orders))
            # the re-arm: Target admits a ticket now; the re-dispatch must re-enter the held cart, not add.
            model.a["primary"]["fs"] = 0
            w.out = io.StringIO()
            atc_before = model.n_calls("primary", "atc")
            ok2 = w.race()
            log2 = w.log()
            check("c_rearm_race_finished_purchased", ok2 and row(m).get("status") == "purchased", str(row(m)))
            check("c_rearm_reentered_through_held_cart_entry", "[WON_CART_DIRECT] start entry=held" in log2
                  and ex.held_entry_calls == [(T, "order_confirmed")], f"{ex.held_entry_calls} {log2[-1500:]}")
            check("c_rearm_fired_no_add_to_cart", model.n_calls("primary", "atc") == atc_before == 1
                  and ex.fl_calls == [QTY] and model.atc_legacy.get("primary", 0) == 0,
                  f"fl={ex.fl_calls} calls={model.calls['primary'][-4:]}")
            check("c_rearm_bought_once_on_the_held_line", [o[0] for o in model.orders] == ["primary"]
                  and model.orders[0][3] == QTY and not model.po_empty and not model.a["primary"]["cart"],
                  f"{model.orders} {model.po_empty}")
            check("c_marker_cleared_after_the_order", ex._held_cart is None, str(ex._held_cart))
            check("c_no_unknown_tab_calls", not unknown(w), str(unknown(w)))
        finally:
            w.close()


# ───────── D: cart_evicted after a FAST_SELLING, in-thread retry inside the 45 s cooldown ─────────

def test_d_evicted_then_retry_inside_cooldown():
    with env(**bat_manager_env()):
        # chain: ATC 201, place-order 429 FAST_SELLING (-> loop); ticket 1: 424 + the cart emptied
        # (-> eviction read -> cart_evicted -> checkout_busy_retryable, transient -> in-thread retry)
        w = mk({"primary": dict(po_plan=["429fs", "424"])})
        try:
            ok = w.race()
            model, ex, m = w.model, w.ex(), w.m
            log = w.log()
            calls = model.calls["primary"]
            check("d_race_finished", ok, str(row(m)))
            check("d_loop_ended_cart_evicted", "[WON_CART_DIRECT] end reason=cart_evicted" in log, log[-1800:])
            check("d_exit_is_checkout_busy_retryable_and_retried_in_thread",
                  "Transient ATC failure 'checkout_busy_retryable' (attempt 1)" in log, log[-1800:])
            check("d_retry_skipped_the_chain", "[THROTTLE] FAST_SELLING cooldown active" in log
                  and ex.fl_calls == [QTY], f"fl={ex.fl_calls} {log[-900:]}")
            check("d_retry_took_the_legacy_atc_path_once", model.atc_legacy.get("primary") == 1
                  and "Firing ATC fetch qty=" in log, f"{model.atc_legacy} {calls}")
            check("d_exact_order_of_requests", calls == ["atc_201", "po_429fs", "po_424", "latc_201"], str(calls))
            check("d_no_double_order", not model.orders and model.po_posts.get(("primary", T)) == 2
                  and not model.po_empty, f"{model.orders} {model.po_posts} {model.po_empty}")
            check("d_legacy_checkout_reached_exactly_once_and_stopped", ex.statuses.count("checking_out") == 1,
                  str(ex.statuses))
            check("d_no_stacking_one_line_at_the_requested_qty", len(model.a["primary"]["cart"]) == 1
                  and model.a["primary"]["cart"][0]["qty"] == QTY, str(model.a["primary"]["cart"]))
            check("d_no_unknown_tab_calls", not unknown(w), str(unknown(w)))
        finally:
            w.close()


# ─────────────────── E: ride-to-OOS tail, the line kept held ───────────────────

def test_e_oos_tail_keeps_the_held_line():
    flip = {"n": 4, "at": None}
    holder = {}

    def on_ticket(model, ident, mode):
        if model.tickets_n[ident] == flip["n"] and flip["at"] is None:
            flip["at"] = model.tickets_n[ident]
            holder["w"].statuses[T] = M._st(in_stock=False)     # the manager's stock_snapshot feeds the probe

    with env(**bat_manager_env()):
        w = mk({"primary": dict(fs=BIG)})
        holder["w"] = w
        try:
            w.model.on_ticket = on_ticket
            ok = w.race()
            model, ex, m = w.model, w.ex(), w.m
            log = w.log()
            check("e_race_finished_failed", ok and row(m).get("status") == "failed", str(row(m)))
            check("e_probe_flipped_mid_ride", flip["at"] == flip["n"], str(flip))
            check("e_exactly_the_tail_ticket_after_oos", model.tickets_n["primary"] == flip["n"] + 1,
                  f"tickets={model.tickets_n} flip={flip}")
            check("e_exit_tail_spent_line_held", "[WON_CART_DIRECT] end reason=tail_spent" in log
                  and "held=yes" in log and " oos=1 " in log, log[-1500:])
            check("e_line_kept_no_delete", ex.deletes == [] and len(model.a["primary"]["cart"]) == 1,
                  f"{ex.deletes} {model.a['primary']['cart']}")
            h = ex._held_cart
            check("e_held_marker_set_not_dirty", isinstance(h, dict) and h.get("tcin") == T
                  and getattr(ex, "_woncart_dirty", None) is None, str(h))
            check("e_racer_ended_result_not_retried", "Reason 'won_cart_held' not transient" in log
                  and "(attempt 2)" not in log, log[-800:])
            check("e_one_chain_no_second_atc", ex.fl_calls == [QTY] and model.n_calls("primary", "atc") == 1,
                  f"{ex.fl_calls}")
            # a dispatch while the stock still reads OOS: nothing may fire, the marker survives.
            w.statuses[T] = M._st(in_stock=False)
            n_calls = len(model.calls["primary"])
            w.out = io.StringIO()
            ok2 = w.race()
            log2 = w.log()
            check("e_dispatch_while_oos_fires_nothing", len(model.calls["primary"]) == n_calls
                  and ex.fl_calls == [QTY] and isinstance(ex._held_cart, dict)
                  and len(model.a["primary"]["cart"]) == 1, f"{ok2} {log2[-900:]}")
            check("e_idle_skip_is_the_named_reason", "held_cart_idle_skip" in log2
                  and ex.held_entry_calls and ex.held_entry_calls[-1] == (T, "held_cart_idle_skip"), log2[-700:])
            # the next flip: the kept line is bought through the held entry, no add-to-cart.
            w.statuses[T] = M._st(win_ago=1.0)
            model.a["primary"]["fs"] = 0
            w.out = io.StringIO()
            ok3 = w.race()
            check("e_next_flip_buys_the_kept_line_without_an_atc", ok3 and row(m).get("status") == "purchased"
                  and [o[0] for o in model.orders] == ["primary"] and model.n_calls("primary", "atc") == 1
                  and ex.fl_calls == [QTY] and "[WON_CART_DIRECT] start entry=held" in w.log(),
                  f"{row(m)} {model.orders} {model.calls['primary'][-3:]}")
            check("e_no_unknown_tab_calls", not unknown(w), str(unknown(w)))
        finally:
            w.close()


def main():
    for fn in (test_a_in_chain_429_enters_the_loop, test_b_in_chain_424_falls_through,
               test_c_budget_spent_held_then_rearm_reentry, test_d_evicted_then_retry_inside_cooldown,
               test_e_oos_tail_keeps_the_held_line):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            H.FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print("  FAIL:", f)
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

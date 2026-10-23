#!/usr/bin/env python3
"""Offline test: the COMPOSED wall-3 chain (S5, readiness audit 2026-10-18).

WHY: every wall-3 piece (the held-line strike, the won-cart ticket loop, AC-1 + PO_2XX
ambiguity, FX-1014-REBUY, the buyers file) is pinned on its own, but never TOGETHER:
real PurchaseExecutors under the real manager race thread, against one shared model of
Target. The audit asked whether the pieces compose, i.e. whether the manager's contract
with the executor's result dicts holds end to end.

What is real here: BulletproofPurchaseManager (_start_real_purchase, the race threads,
_thread_skip_reason, _update_purchase_result / _record_race_result, the AC-1 latch, the
REBUY buyers file + restore), and three PurchaseExecutors (execute_purchase ->
_execute_purchase_impl -> the won-cart ticket loop, the strike paths, the success tail).
What is a model: Target. The in-browser fast-lane chain (ATC -> pre_checkout -> place-order)
and each ticket are answered from ONE shared state machine (carts, edge limiter, the
FAST_SELLING gate, the order endpoint); nothing else is stubbed in the executors. Executor
time is a fake clock (ticket cadence is seconds); the manager runs in real time.

Scenarios:
  A  primary holds a kept line from window 1 and strikes it (ATC 400 MAX_PURCHASE ->
     pre_checkout -> place-order); business + alt-1 draw edge 429 x3, an ATC 201 and FAST_SELLING
     pre_checkout 429s before a 200 ticket. At most one place-order POST per account x TCIN,
     the buyer sits out (bought_this_window), REBUY keeps the others racing, the state is
     'purchased' with units = the sum, the buyers file is written, the AC-1 latch is untouched.
     A2 is the same with the line tracked by a dirty marker (the strike goes through the loop).
  B  a place-order that returns 5xx (then, were it re-fired, a 2xx-other), and the mirror: the
     loop stops, the AC-1 latch is written, no further ticket for that account x TCIN even on
     the next race; the other accounts are unaffected.
  C  restart: a new manager with the buyers file present; the buyer does not re-race.

Offline: no browser, no network. Run: python tests/test_wall3_chain_1018.py
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

import test_identity_rest as H  # noqa: E402
import test_mass_restock_1006 as M  # noqa: E402
import test_fast_cadence_1011 as FC  # noqa: E402  (_bat_env: the production wrapper's plain `set` lines)
import test_won_cart_direct_smoke as S  # noqa: E402  (import only; its main() does not run)
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402

check = H.check
env = H.env
T = S.TCIN
QTY = 2
MAXP_BODY = json.dumps({"message": "Items cannot be added to cart as max purchase limit exceeded",
                        "code": "MAX_PURCHASE_LIMIT_EXCEEDED"})
_TMP = tempfile.mkdtemp(prefix="wall3_1018_")
IDENTS = ("primary", "business", "alt-1")

# The armed bundle the executors and the manager see (the bat's values for the wall-3 chain).
EXEC_ENV = dict(
    TARGET_API_PLACE_ORDER="true", TARGET_FAST_LANE="1", TARGET_STOCK_PROBE="1",
    TARGET_WONCART_DIRECT="1", TARGET_WONCART_RF_ENTRY="1", TARGET_AMBIGUOUS_COMMIT_LATCH="1",
    TARGET_AMBIGUOUS_COMMIT_LATCH_S="1800", TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST="1",
    TARGET_FASTLANE_QTY_GUARD="1", TARGET_HELD_CART_REENTRY="1", TARGET_HELD_CART_TTL_S="3600",
    TARGET_HELD_LINE_FLIP_STRIKE="1", TARGET_PO_2XX_AMBIGUOUS="1", TARGET_FOREIGN_KEEP_WON="1",
    TARGET_WONCART_SCHEDULE_S="1,1,1,2,2,3", TARGET_WONCART_STEADY_GAP_S="3",
    TARGET_WONCART_JITTER_S="1", TARGET_WONCART_OOS_TAIL_TICKETS="1",
    TARGET_WONCART_MAX_TICKETS="40", TARGET_WONCART_CALL_MAX_S="120",
    TARGET_WONCART_HEADROOM_S="45", TARGET_WONCART_YIELD_FLEET="0",
    TARGET_WONCART_LIVE_CAP_EXEMPT="1", TARGET_WONCART_HARD_MAX_TICKETS="1200",
    TARGET_WONCART_RIDE_TO_OOS="1", TARGET_WONCART_EVICTION_READ="1",
    TARGET_WONCART_EVICTION_PRESUME="1", TARGET_WON_CART_RIDE="1",
    TARGET_WON_CART_RIDE_CLEAN_EXIT="1", TARGET_WON_CART_RIDE_MAX_S="300",
    TARGET_FAST_SELLING_COOLDOWN_S="45", TARGET_QTY_CEILING="2",
)
# The manager side: REBUY needs MISS_REARM; the race loop retries edge 429s at a 1 s floor.
MGR_ENV = dict(
    TARGET_REBUY_NONBUYERS="1", TARGET_MULTI_SKU_MISS_REARM="1", TARGET_MULTI_SKU_FAIR_SHARE="1",
    TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0",
    TARGET_ATC_EDGE429_RETRY_DELAY_MIN="2.0", TARGET_ATC_EDGE429_RETRY_DELAY_MAX="3.0",
    TARGET_RETRY_WARM="0", TARGET_WAVE_FIRST_ONLY="1", TARGET_SHOT_BANK_GATE="1",
    TARGET_401_PULSE="1", TARGET_RACE_ALL_WORKERS="1", TARGET_RETRY_WHILE_IN_STOCK="1",
    TARGET_RETRY_WHILE_IN_STOCK_MAX="40", TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="110",
    TARGET_ATC_DCO_BURST="1", TARGET_RETRY_STOP_WHEN_OOS="1", TARGET_FAST_CADENCE_IDENTS="alt-1",  # 10-23: re-armed alt-1 A/B (C-1023-26)
    TARGET_RACE_JOIN_FREE=None, TARGET_FAIR_FRESH_FIX=None, TARGET_PARK_ACCOUNT_TCINS=None,
)


# 1019b (pins lane, T2): the EXEC_ENV / MGR_ENV literals above are tied to the production wrapper.
# Before 1019b nine MGR_ENV literals diverged from the bat (EDGE429 retry delay 1.0/1.0 vs 2.0/3.0,
# WAVE_FIRST_ONLY 0 vs 1, SHOT_BANK_GATE 0 vs 1, 401_PULSE 0 vs 1, RETRY_WHILE_IN_STOCK_MAX 6 vs 40,
# ATC_DCO_BURST 0 vs 1, RETRY_STOP_WHEN_OOS 0 vs 1, FAST_CADENCE_IDENTS unset vs all) and the three
# scenarios below still passed with the bat's values. Names the bat does not set are left to code
# defaults / app.py (`os.environ.setdefault('TARGET_API_PLACE_ORDER', 'true')`); exactly these three are
# allow-listed. The other bat-absent MGR_ENV names (ATC_RETRY_DELAY_MIN/MAX = 0 vs the code default
# 2.5/3.5, RACE_ALL_WORKERS, RETRY_WHILE_IN_STOCK, RETRY_WHILE_IN_STOCK_BUDGET_S, and the None literals)
# are not compared (the spec ties only names the bat also sets) and are printed as [NOTE] lines.
BAT_ABSENT_ALLOWED = ("TARGET_API_PLACE_ORDER", "TARGET_FASTLANE_QTY_GUARD", "TARGET_QTY_CEILING")


def env_tie_problems(bat, exec_env, mgr_env):
    """(mismatches, allowlist_now_set_by_bat, notes) for the two literal dicts vs a bat `set` map."""
    bad, stale, notes = [], [], []
    for nm, d in (("EXEC_ENV", exec_env), ("MGR_ENV", mgr_env)):
        for k, v in d.items():
            if k in bat:
                if v != bat[k]:
                    bad.append((nm, k, v, bat[k]))
            elif k not in BAT_ABSENT_ALLOWED and v is not None:
                notes.append((nm, k, v))
    for k in BAT_ABSENT_ALLOWED:
        if k in bat:
            stale.append(k)
    return bad, stale, notes


def test_env_tied_to_bat():
    bat = FC._bat_env()
    check("t2_bat_env_parsed", len(bat) > 100 and bat.get("TARGET_HELD_LINE_FLIP_STRIKE") == "1", len(bat))
    bad, stale, notes = env_tie_problems(bat, EXEC_ENV, MGR_ENV)
    check("t2_every_bat_set_literal_equals_the_bat", not bad, bad)
    check("t2_allowlist_names_are_absent_from_the_bat", not stale, stale)
    check("t2_allowlist_is_exactly_three", BAT_ABSENT_ALLOWED == (
        "TARGET_API_PLACE_ORDER", "TARGET_FASTLANE_QTY_GUARD", "TARGET_QTY_CEILING"))
    for nm, k, v in notes:
        print(f"[NOTE] {nm}.{k}={v!r} is not set by the bat (code default applies in production)")
    # the pin bites: a drifted literal, a bat that starts setting an allow-listed name, a flipped bat value
    b, _, _ = env_tie_problems(bat, dict(EXEC_ENV, TARGET_WONCART_YIELD_FLEET="1"), MGR_ENV)
    check("t2_mutation_exec_literal_drift_caught", [x[1] for x in b] == ["TARGET_WONCART_YIELD_FLEET"], b)
    b, _, _ = env_tie_problems(bat, EXEC_ENV, dict(MGR_ENV, TARGET_ATC_DCO_BURST="0"))
    check("t2_mutation_mgr_literal_drift_caught", [x[1] for x in b] == ["TARGET_ATC_DCO_BURST"], b)
    _, st, _ = env_tie_problems(dict(bat, TARGET_QTY_CEILING="2"), EXEC_ENV, MGR_ENV)
    check("t2_mutation_allowlist_goes_stale_caught", st == ["TARGET_QTY_CEILING"], st)
    b, _, _ = env_tie_problems(dict(bat, TARGET_RETRY_STOP_WHEN_OOS="0"), EXEC_ENV, MGR_ENV)
    check("t2_mutation_bat_value_flip_caught", [x[1] for x in b] == ["TARGET_RETRY_STOP_WHEN_OOS"], b)


def _all_env(extra=None):
    e = {**M.FLAGS, **M.MSKU, **EXEC_ENV, **MGR_ENV}
    # 10-22: the wrapper carries TARGET_FAST_CADENCE_IDENTS=0 (C-1022-01); the MGR_ENV literal mirrors it for the
    # drift checks, but these virtual-time chain scenarios were written for the fast-cadence race (slow re-entry
    # leaves the third racer asleep), so the scenario env forces the fast path explicitly.
    e["TARGET_FAST_CADENCE_IDENTS"] = "all"
    e.update(extra or {})
    return e


# ───────────────────────────── the Target model ──────────────────────────────

class Target:
    """One Target for all three accounts. Per-account state: the cart, the edge limiter
    (the first `edge` add-to-carts are 429'd), the FAST_SELLING gate (the first `fs`
    checkout-level requests are 429'd BEFORE the cart is looked at) and a place-order
    plan (the first N order POSTs answer as planned instead of by the cart)."""

    def __init__(self, qty=QTY):
        self.qty = qty
        self.lk = threading.Lock()
        self.a = {}
        self.po_posts = {}       # (ident, tcin) -> order POSTs that reached the order endpoint
        self.po_empty = {}       # ... of which found no line of ours in the cart
        self.orders = []         # (ident, tcin, order_id, qty)
        self.calls = {}          # ident -> ["atc_edge", "atc_400", "atc_201", "pre_fs", "po_200", ...]
        self.timeline = []       # (ident, what) across accounts, in arrival order
        self.seq = 0

    def account(self, ident, cart=(), edge=0, fs=0, po_plan=()):
        self.a[ident] = {"cart": [dict(c) for c in cart], "edge": edge, "fs": fs,
                         "po_plan": list(po_plan), "ordered": {}}
        self.calls[ident] = []

    @staticmethod
    def line(tcin=T, qty=QTY, ident="x"):
        return {"id": f"CI-{ident}-{tcin}", "tcin": tcin, "qty": qty}

    def _note(self, ident, what):
        self.calls[ident].append(what)
        self.timeline.append((ident, what))

    def n_calls(self, ident, what=None):
        c = self.calls.get(ident, [])
        return len(c) if what is None else sum(1 for x in c if x.startswith(what))

    def _lines(self, a, tcin):
        return [c for c in a["cart"] if c["tcin"] == tcin]

    def _po(self, ident, tcin):
        """The order endpoint. Returns (status, body). Caller holds the lock."""
        a = self.a[ident]
        key = (ident, tcin)
        self.po_posts[key] = self.po_posts.get(key, 0) + 1
        lines = self._lines(a, tcin)
        if a["po_plan"]:
            kind = a["po_plan"].pop(0)
            self._note(ident, f"po_{kind}")
            if kind == "503":
                return 503, "<html>bad gateway</html>"
            if kind == "202":
                return 202, "{}"
            if kind == "429fs":
                return 429, S.FS_BODY
        if not lines:
            self.po_empty[key] = self.po_empty.get(key, 0) + 1
            self._note(ident, "po_424")
            return 424, json.dumps({"errorKey": "RESERVATION_FAILURE"})
        n = sum(int(c["qty"]) for c in lines)
        a["cart"] = [c for c in a["cart"] if c["tcin"] != tcin]
        a["ordered"][tcin] = a["ordered"].get(tcin, 0) + n
        self.seq += 1
        oid = f"OID-{ident}-{self.seq}"
        self.orders.append((ident, tcin, oid, n))
        self._note(ident, "po_200")
        return 200, json.dumps({"orders": [{"order_id": oid, "reference_id": f"R{self.seq}"}]})

    # -- the in-browser fast-lane chain (what _api_fast_lane's JS returns) ----------
    def fast_lane(self, ident, tcin, qty, strike_on):
        with self.lk:
            a = self.a[ident]
            nopo = {"status": 0, "body": "", "fired": False}
            if a["edge"] > 0:
                a["edge"] -= 1
                self._note(ident, "atc_edge")
                return {"atc": {"status": 429, "body": "", "cart_items": []},
                        "pre": {"status": 0}, "po": nopo, "skip": "atc_429"}
            held = self._lines(a, tcin)
            if held:                                   # MAX_PURCHASE_LIMIT_EXCEEDED on a kept line
                self._note(ident, "atc_400")
                if not strike_on:
                    return {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": []},
                            "pre": {"status": 0}, "po": nopo, "skip": "atc_400"}
                atc = {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True}
            else:
                self._note(ident, "atc_201")
                a["cart"].append(self.line(tcin, qty, ident))
                atc = {"status": 201, "body": "", "cart_items": [{"tcin": tcin, "quantity": qty}]}
            if a["fs"] > 0:
                a["fs"] -= 1
                self._note(ident, "pre_fs")
                return {"atc": atc, "pre": {"status": 429, "body": S.FS_BODY},
                        "po": nopo, "skip": "pre_429"}
            lines = self._lines(a, tcin)
            pre = {"status": 200 if not atc.get("strike") else 201, "n": len(a["cart"]),
                   "tcins": [c["tcin"] for c in a["cart"]],
                   "qty": sum(int(c["qty"]) for c in lines), "pi": []}
            status, body = self._po(ident, tcin)
            return {"atc": atc, "pre": pre, "po": {"status": status, "body": body, "fired": True},
                    "skip": ""}

    # -- one won-cart ticket (what the ticket JS returns) ---------------------------
    def ticket(self, ident, mode):
        with self.lk:
            a = self.a[ident]
            lines = self._lines(a, T)
            if mode == "pre_po":
                if a["fs"] > 0:
                    a["fs"] -= 1
                    self._note(ident, "pre_fs")
                    return S.t_pre(429, S.FS_BODY)
                if not lines:
                    self._note(ident, "pre_400")
                    return S.t_pre(400, "{}")
                status, body = self._po(ident, T)
                rej = (429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION") if status == 429 else None
                return S.t_prepo(status, body, rej=rej)
            # po_only
            if a["fs"] > 0:
                a["fs"] -= 1
                self._note(ident, "po_fs")
                self.po_posts[(ident, T)] = self.po_posts.get((ident, T), 0) + 1
                return S.t_po(429, S.FS_BODY, rej=(429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"))
            status, body = self._po(ident, T)
            return S.t_po(status, body)

    # -- cart endpoints ---------------------------------------------------------------
    def cart_read(self, ident):
        with self.lk:
            a = self.a[ident]
            return {"ok": True, "status": 200, "has_items": bool(a["cart"]),
                    "items": [dict(c) for c in a["cart"]]}

    def delete(self, ident, only_tcin=None, keep_tcin=None, ids=None):
        with self.lk:
            a = self.a[ident]
            n0 = len(a["cart"])
            if ids is not None:
                a["cart"] = [c for c in a["cart"] if c["id"] not in {str(i) for i in ids}]
            elif only_tcin is not None:
                a["cart"] = [c for c in a["cart"] if c["tcin"] != str(only_tcin)]
            elif keep_tcin is not None:
                a["cart"] = [c for c in a["cart"] if c["tcin"] == str(keep_tcin)]
            else:
                a["cart"] = []
            return True, n0 - len(a["cart"])


class ModelTab(S.TicketTab):
    """The account's tab: every evaluate is answered from the shared Target model."""

    def __init__(self, ex, model, ident, clock):
        super().__init__(ex, [], clock)
        self.model, self.ident = model, ident
        self.handlers, self.enabled_domains, self.gets = {}, [], []
        self.unknown = []

    async def get(self, url):
        self.gets.append(url)

    async def send(self, *a, **k):
        return None

    async def select(self, sel, timeout=None):
        return None

    async def find(self, text, best_match=True, timeout=None):
        return None

    async def evaluate(self, js, await_promise=False, **kw):
        if "e.abort = true" in js:
            self.aborts += 1
            return None
        if "const MODE = '" in js:
            mode = re.search(r"const MODE = '(\w+)'", js).group(1)
            self.tickets.append((self.clock.t, mode, js))
            step = self.model.ticket(self.ident, mode)
            rej = step.get("rej")
            if rej:
                self.ex._checkout_rejected = True
                self.ex._checkout_reject_status, self.ex._checkout_reject_reason = rej
            return copy.deepcopy(step["out"])
        if "method: 'DELETE'" in js:
            cid = re.search(r"cart_items/([A-Za-z0-9_-]+)'", js).group(1)
            self.model.delete(self.ident, ids=[cid])
            return 204
        if "cart_item_id).filter(Boolean)" in js:           # _api_clear_cart's GET
            return {"ok": True, "status": 200,
                    "ids": [c["id"] for c in self.model.cart_read(self.ident)["items"]]}
        if "has_items" in js or "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            return self.model.cart_read(self.ident)
        if "filter(Boolean)" in js:                          # _check_cart_hold
            r = self.model.cart_read(self.ident)
            out = {"ok": True, "tcins": [i["tcin"] for i in r["items"]]}
            if "n: Array.isArray(d.cart_items)" in js:
                out["n"] = len(r["items"])
            return out
        self.unknown.append(js[:100])
        return None


class _SMW(S._SM):
    """The executor stub session manager, widened to what the manager touches."""
    session_active = True

    def __init__(self, ident, cvv=""):
        super().__init__(cvv)
        self.account_id = ident
        self.browser = object()

    def set_purchase_in_progress(self, v):
        pass

    async def refresh_session(self):
        return True


def make_exec(ident, clock, model):
    """A REAL PurchaseExecutor (S.impl_ex's bare instance) wired to the Target model:
    _api_fast_lane answers from the model, the won-cart loop is the real one, the tab is the
    model's, cart deletes act on the model's cart."""
    ex, _ = S.impl_ex(clock, {"atc": {"status": 0}})
    del ex._won_cart_ticket_loop                         # the REAL loop, not the harness stub
    sm = _SMW(ident)
    ex.session_manager = sm
    tab = ModelTab(ex, model, ident, clock)

    async def _gp():
        return tab

    sm.get_page = _gp
    ex.tab = tab
    ex._page_lock = asyncio.Lock()
    ex._purchase_timeout_ctx = None
    for k, v in dict(_cart_hold_check_on=True, _cart_hold_check_interval_s=0.0,
                     _last_cart_hold_check_ts=0.0, _cart_hold_verbose=True, _cart_hold_skip_edge=True,
                     _atc_401_ladder_on=False, _tcin_throttle_cooldown_s=90.0).items():
        setattr(ex, k, v)

    async def _fl(t, tcin, qty, hdrs):
        ex.fl_calls.append(qty)
        return copy.deepcopy(model.fast_lane(ident, str(tcin), qty,
                                             bool(ex._held_line_strike_active(str(tcin)))))

    ex._api_fast_lane = _fl

    async def _del(t, only_tcin=None, keep_tcin=None, budget_s=15.0, ids=None, abort_fn=None):
        ex.deletes.append({"only": only_tcin, "keep": keep_tcin})
        return model.delete(ident, only_tcin=only_tcin, keep_tcin=keep_tcin, ids=ids)

    ex._delete_cart_items = _del

    async def _warm(force_fresh=False):
        return True

    ex.warm_shape_headers = _warm
    return ex


class Worker:
    def __init__(self, wid, ident, ex):
        self.cfg = H._Cfg(wid, ident)
        self.session_manager = ex.session_manager
        self.purchase_executor = ex

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

    def run_async(self, coro):
        import concurrent.futures
        f = concurrent.futures.Future()
        try:
            f.set_result(asyncio.run(coro))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f


class Fresh(threading.Thread):
    """Keeps the monitor's read of T 'just now' (the stock probe has a freshness limit)."""

    def __init__(self, statuses):
        super().__init__(daemon=True)
        self.s, self.stop_ev = statuses, threading.Event()

    def run(self):
        while not self.stop_ev.wait(0.1):
            st = self.s.get(T)
            if st is not None and st.in_stock:
                st.last_checked_at = st.last_true_at = time.time()


class World:
    """3 real executors + a real manager on one Target model, with a shared fake executor clock."""

    def __init__(self, model, rebuy_file, latch_file, statuses=None):
        self.model = model
        self.clock = S.Clock(time.time())
        self.exs = {i: make_exec(i, self.clock, model) for i in IDENTS}
        self.ws = [Worker(n + 1, i, self.exs[i]) for n, i in enumerate(IDENTS)]
        self.statuses = statuses if statuses is not None else {T: M._st(win_ago=40.0)}
        self.rebuy_file, self.latch_file = rebuy_file, latch_file
        self.m = self.new_manager()
        self.fresh = Fresh(self.statuses)
        self.fresh.start()
        self.out = io.StringIO()

    def new_manager(self):
        """A manager as __init__ builds one: buyers + AC-1 latches restored from their files."""
        m = H.race_mgr(self.ws)
        m._warmup_cycle_counter = 5
        m._maybe_run_session_sentinel = lambda: None
        m.use_real_purchasing = True
        m.start_purchase = lambda tcin, title, max_qty=1: bpm_mod.BulletproofPurchaseManager._start_real_purchase(
            m, tcin, title, m._load_states_unsafe(), max_qty=max_qty)
        M._with_checker(m, self.statuses)
        m._ac_error_log_path = os.path.join(tempfile.mkdtemp(dir=_TMP), "error_log.txt")
        with env(TARGET_REBUY_FILE=self.rebuy_file, TARGET_AMBIGUOUS_COMMIT_LATCH_FILE=self.latch_file):
            m._rebuy_restore()
            m._ac_restore_latches()
            m._rebuy_path = self.rebuy_file
        return m

    def race(self, m=None, timeout=60):
        """Dispatch one race for T and wait until it is final (the row leaves 'attempting' when the
        last racer's result is recorded) and every racer thread has ended."""
        m = m or self.m
        with contextlib.redirect_stdout(self.out):
            with S.fake_time(self.clock):
                m._start_real_purchase(T, "Tin", m._load_states_unsafe(), max_qty=QTY)
                deadline = time.time() + timeout
                ok = False
                while time.time() < deadline:
                    if (m._load_states_unsafe().get(T) or {}).get("status") != "attempting":
                        ok = True
                        break
                    time.sleep(0.05)
                while time.time() < deadline and any(
                        i["thread"].is_alive() for i in list(m._active_purchases.values())):
                    time.sleep(0.05)
                time.sleep(0.2)
        return ok

    def close(self):
        self.fresh.stop_ev.set()

    def log(self):
        return self.out.getvalue()


def _mk(accts, **kw):
    model = Target()
    for ident, spec in accts.items():
        model.account(ident, **spec)
    d = tempfile.mkdtemp(dir=_TMP)
    return World(model, os.path.join(d, "rebuy_buyers.json"), os.path.join(d, "ac_latch.json"), **kw)


def _buyers(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("buyers", {})
    except Exception:
        return None


def _row(m):
    return (m._load_states_unsafe() or {}).get(T, {})


# ───────────────────────── scenario A: strike + tickets ─────────────────────────

def _scenario_a(variant):
    kept = Target.line(T, QTY, "primary")
    accts = {"primary": dict(cart=[kept]),
             "business": dict(edge=3, fs=4),
             "alt-1": dict(edge=3, fs=4)}
    with env(**_all_env()):
        w = _mk(accts)
        try:
            if variant == "dirty":
                w.exs["primary"]._woncart_dirty = {"tcin": T, "ts": w.clock.t - 900, "why": "cart_ticket_cap",
                                                   "cvv_put": "none"}
            ok = w.race()
            m, model = w.m, w.model
            tag = f"A_{variant}"
            row = _row(m)
            check(f"{tag}_race_finished", ok and row.get("status") == "purchased", f"ok={ok} row={row}")
            check(f"{tag}_all_three_ordered", sorted(o[0] for o in model.orders) == sorted(IDENTS),
                  f"orders={model.orders}\n{w.log()[-1500:]}")
            check(f"{tag}_at_most_one_order_post_per_account",
                  all(model.po_posts.get((i, T), 0) <= 1 for i in IDENTS) and not model.po_empty,
                  f"posts={model.po_posts} empty={model.po_empty} calls={model.calls}")
            check(f"{tag}_units_are_the_sum", row.get("units_bought") == QTY * 3
                  and sum(o[3] for o in model.orders) == QTY * 3, f"row={row} orders={model.orders}")
            check(f"{tag}_order_numbers_recorded", len(row.get("order_numbers") or []) == 3, str(row))
            if variant == "kept":
                check(f"{tag}_primary_struck_on_the_kept_line",
                      model.calls["primary"][:2] == ["atc_400", "po_200"], str(model.calls["primary"]))
            else:
                check(f"{tag}_primary_struck_through_the_loop_no_atc",
                      model.n_calls("primary", "atc") == 0 and model.calls["primary"][-1] == "po_200",
                      str(model.calls["primary"]))
            check(f"{tag}_others_drew_edge_429s_then_a_201_then_fs",
                  model.calls["business"][:5] == ["atc_edge"] * 3 + ["atc_201", "pre_fs"]
                  and model.calls["business"][-1] == "po_200", str(model.calls["business"]))
            check(f"{tag}_fs_gate_spent_in_tickets", model.n_calls("business", "pre_fs") == 4
                  and model.n_calls("alt-1", "pre_fs") == 4, str(model.calls))
            check(f"{tag}_no_ambiguity_latch", not getattr(m, "_ac_latch", None)
                  and "[AMBIGUOUS_COMMIT]" not in w.log(), str(getattr(m, "_ac_latch", None)))
            # REBUY keeps the others racing after the buyer's success, and the buyer is done:
            tl = model.timeline
            i_p = max(k for k, (i, _) in enumerate(tl) if i == "primary")
            i_b = min(k for k, (i, w_) in enumerate(tl) if i == "business" and w_ == "po_200")
            check(f"{tag}_primary_ordered_first_others_kept_racing",
                  tl[i_p] == ("primary", "po_200") and i_p < i_b and "[REBUY] " in w.log()
                  and any(i != "primary" for i, _ in tl[i_p + 1:]), str(tl))
            check(f"{tag}_buyer_fired_nothing_after_its_order",
                  all(i != "primary" for i, _ in tl[i_p + 1:]), str(tl))
            br = row.get("race_breakdown") or {}
            check(f"{tag}_breakdown_all_purchased", set(br.values()) == {"purchased"}, str(br))
            b = _buyers(w.rebuy_file)
            check(f"{tag}_buyers_file_written",
                  b is not None and set(b) == {f"W{n + 1}/{i}|{T}" for n, i in enumerate(IDENTS)}, str(b))
            # The buyer sits out; REBUY keeps the others on the TCIN while it reads in stock.
            for n, i in enumerate(IDENTS):
                check(f"{tag}_{i}_sits_out_the_window",
                      m._thread_skip_reason(f"W{n + 1}/{i}", i, T, log=False) == "bought_this_window")
            check(f"{tag}_no_unknown_tab_calls", not any(w.exs[i].tab.unknown for i in IDENTS),
                  str({i: w.exs[i].tab.unknown for i in IDENTS}))
        finally:
            w.close()


def test_scenario_a_strike_and_tickets():
    _scenario_a("kept")
    _scenario_a("dirty")


# ───────────────────── scenario B: an unresolved place-order mid-ride ─────────────────────

def _scenario_b(plan, others_edge):
    """`business` rides a FAST_SELLING cart and its place-order answers `plan` (a 5xx, then a
    2xx-other, or the mirror); primary strikes a kept line and buys; alt-1 draws `others_edge`
    edge 429s. The unresolved POST must end business's loop: latch, no further ticket."""
    tag = f"B_{'_'.join(plan)}"
    kept = Target.line(T, QTY, "primary")
    accts = {"primary": dict(cart=[kept]),
             "business": dict(fs=2, po_plan=plan),
             "alt-1": dict(edge=others_edge, fs=1)}
    with env(**_all_env({"TARGET_RETRY_WHILE_IN_STOCK_MAX": "3"})):
        w = _mk(accts)
        try:
            ok = w.race()
            m, model = w.m, w.model
            row = _row(m)
            log = w.log()
            check(f"{tag}_race_finished", ok and row.get("status") == "purchased", f"ok={ok} row={row}")
            b_calls = model.calls["business"]
            check(f"{tag}_business_loop_stopped_at_the_unresolved_post",
                  model.po_posts.get(("business", T)) == 1 and b_calls[-1] == f"po_{plan[0]}"
                  and model.a["business"]["po_plan"] == list(plan[1:]), f"calls={b_calls} plan={model.a['business']['po_plan']}")
            check(f"{tag}_business_has_no_order", not any(o[0] == "business" for o in model.orders),
                  str(model.orders))
            check(f"{tag}_latch_written", m._ac_latched_left("W2/business", T) > 1000
                  and "[AMBIGUOUS_COMMIT] W2/business" in log, f"{m._ac_latch} {log[-1200:]}")
            lat = None
            try:
                with open(w.latch_file, "r", encoding="utf-8") as f:
                    lat = f.read()
            except Exception:
                pass
            check(f"{tag}_latch_persisted", lat is not None and "W2/business" in lat and T in lat, str(lat))
            check(f"{tag}_latch_is_business_only", [k for k in m._ac_latch] == [("W2/business", T)],
                  str(m._ac_latch))
            check(f"{tag}_ambiguous_flag_reached_the_manager",
                  (row.get("race_breakdown") or {}).get("W2/business") == "checkout_navigation_failed"
                  and "[AMBIGUOUS_COMMIT] W2/business 1010892069 latched" in log
                  and "why=checkout_navigation_failed" in log, str(row.get("race_breakdown")))
            b = _buyers(w.rebuy_file) or {}
            check(f"{tag}_ac1_also_a_buyer_record_for_the_window",
                  f"W2/business|{T}" in b and f"W1/primary|{T}" in b, str(b))
            check(f"{tag}_primary_unaffected_bought", any(o[0] == "primary" for o in model.orders),
                  str(model.orders))
            # The next race: business is latched, primary bought; nothing may fire for them.
            n_before = {i: len(model.calls[i]) for i in IDENTS}
            ok2 = w.race()
            m2row = _row(m)
            check(f"{tag}_next_race_finished", ok2, str(m2row))
            check(f"{tag}_next_race_business_fires_nothing", len(model.calls["business"]) == n_before["business"]
                  and model.po_posts.get(("business", T)) == 1, str(model.calls["business"]))
            check(f"{tag}_next_race_primary_fires_nothing", len(model.calls["primary"]) == n_before["primary"],
                  str(model.calls["primary"]))
            check(f"{tag}_next_race_alt1_unaffected_races_and_buys",
                  any(o[0] == "alt-1" for o in model.orders) and model.po_posts.get(("alt-1", T), 0) <= 1
                  and not model.po_empty, f"{model.calls['alt-1']} {model.orders} {model.po_empty}")
            check(f"{tag}_next_race_dispatch_leaves_the_sit_outs_idle",
                  set((m2row.get("race_breakdown") or {})) == {"W3/alt-1"}, str(m2row))
            # With the multi-SKU reservation off the sit-outs ARE dispatched and end in-thread, each
            # with its own reason (the latch, the buyer record); nothing reaches Target.
            n_mid = {i: len(model.calls[i]) for i in IDENTS}
            w.out = io.StringIO()
            with env(TARGET_MULTI_SKU_DISPATCH=None, TARGET_MULTI_SKU_FAIR_SHARE=None):
                ok3 = w.race()
            row3 = _row(m)
            br3 = row3.get("race_breakdown") or {}
            check(f"{tag}_third_race_all_sit_out_in_thread", ok3 and row3.get("status") == "failed"
                  and br3 == {"W1/primary": "bought_this_window", "W2/business": "ambiguous_commit_latched",
                              "W3/alt-1": "bought_this_window"}, f"{row3} {w.log()[-900:]}")
            check(f"{tag}_third_race_nothing_reached_target",
                  {i: len(model.calls[i]) for i in IDENTS} == n_mid, str(model.calls))
            check(f"{tag}_sit_out_lines_named", "W2/business sits out" in w.log()
                  and "ambiguous_commit_latched" in w.log(), w.log()[-800:])
            check(f"{tag}_no_unknown_tab_calls", not any(w.exs[i].tab.unknown for i in IDENTS),
                  str({i: w.exs[i].tab.unknown for i in IDENTS}))
        finally:
            w.close()


def test_scenario_b_unresolved_po_mid_ride():
    _scenario_b(("503", "202"), others_edge=3)      # 5xx first, a 2xx-other behind it
    _scenario_b(("202", "503"), others_edge=3)      # PO_2XX_AMBIGUOUS first


# ─────────────────────── scenario C: restart with the buyers file ───────────────────────

def test_scenario_c_restart_with_buyers_file():
    kept = Target.line(T, QTY, "primary")
    accts = {"primary": dict(cart=[kept]),
             "business": dict(edge=2),
             "alt-1": dict(edge=2)}
    with env(**_all_env({"TARGET_RETRY_WHILE_IN_STOCK_MAX": "2"})):
        w = _mk(accts)
        try:
            ok = w.race()
            model = w.model
            check("C_first_run_primary_bought_others_edge_failed",
                  ok and [o[0] for o in model.orders] == ["primary"]
                  and model.n_calls("business", "atc_edge") == 2, f"{model.orders} {model.calls}")
            b = _buyers(w.rebuy_file)
            check("C_buyers_file_holds_the_buyer", b is not None and f"W1/primary|{T}" in b, str(b))
            n_primary = len(model.calls["primary"])
            # RESTART: a brand-new manager (BulletproofPurchaseManager.__init__ restores the file).
            w.out = io.StringIO()
            with contextlib.redirect_stdout(w.out):
                m2 = w.new_manager()
            check("C_restored_line_printed", "[REBUY] restored 1 buyer record" in w.log() and "W1/primary" in w.log(),
                  w.log()[-600:])
            check("C_restart_buyer_binds", m2._thread_skip_reason("W1/primary", "primary", T, log=False)
                  == "bought_this_window")
            check("C_restart_others_free", m2._thread_skip_reason("W2/business", "business", T, log=False) == ""
                  and m2._thread_skip_reason("W3/alt-1", "alt-1", T, log=False) == "")
            ok2 = w.race(m2)
            check("C_restart_race_finished", ok2, w.log()[-600:])
            check("C_restart_buyer_does_not_re_race", len(model.calls["primary"]) == n_primary
                  and model.po_posts.get(("primary", T)) == 1, str(model.calls["primary"]))
            check("C_restart_others_race_and_buy", sorted(o[0] for o in model.orders)
                  == ["alt-1", "business", "primary"] and not model.po_empty
                  and all(model.po_posts.get((i, T), 0) == 1 for i in IDENTS),
                  f"{model.orders} {model.po_posts} {model.po_empty}")
            row = _row(m2)
            check("C_restart_state_purchased", row.get("status") == "purchased"
                  and row.get("units_bought") == QTY * 2
                  and set(row.get("race_breakdown") or {}) == {"W2/business", "W3/alt-1"}, str(row))
            # In-thread (reservation off) the restored buyer is dispatched and ends on its record.
            n_mid = {i: len(model.calls[i]) for i in IDENTS}
            w.out = io.StringIO()
            with env(TARGET_MULTI_SKU_DISPATCH=None, TARGET_MULTI_SKU_FAIR_SHARE=None):
                ok3 = w.race(m2)
            row3 = _row(m2)
            check("C_restart_in_thread_sit_out_named", ok3 and "W1/primary sits out" in w.log()
                  and "bought_this_window" in w.log() and (row3.get("race_breakdown") or {}).get("W1/primary")
                  == "bought_this_window", f"{row3} {w.log()[-800:]}")
            check("C_restart_third_race_nothing_reached_target",
                  {i: len(model.calls[i]) for i in IDENTS} == n_mid, str(model.calls))
            check("C_no_unknown_tab_calls", not any(w.exs[i].tab.unknown for i in IDENTS),
                  str({i: w.exs[i].tab.unknown for i in IDENTS}))
        finally:
            w.close()


# ─────────────────────────────────── main ───────────────────────────────────────

def main():
    for fn in (test_env_tied_to_bat, test_scenario_a_strike_and_tickets, test_scenario_b_unresolved_po_mid_ride,
               test_scenario_c_restart_with_buyers_file):
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

#!/usr/bin/env python3
"""Offline test: FX-1001-A held-line strike (TARGET_HELD_LINE_FLIP_STRIKE).

WHY (docs/CLAIMS.md C-1001-02..07, 2026-10-01 post-run): on the 10-01 restock our
own code threw away lines we already held at the moments they could have been
ordered. A dirty flag deleted (429) and fired nothing at the 02:37:52 restock,
and an add-to-cart 400 MAX_PURCHASE_LIMIT_EXCEEDED ran the self-heal that wiped
the cart. With the flag on, the add-to-cart is the probe: a 400 MAX_PURCHASE
continues in the same chain to pre_checkout -> place-order on the held line.

What must hold (money safety first):
  * flag off: the fast-lane JS is byte-identical to the unset render (golden);
  * the strike places an order ONLY when pre_checkout shows a line of our TCIN,
    and the foreign-item and qty guards still stop it;
  * any other 400 never continues; an account that already ordered the TCIN
    never strikes it; the account scope is honoured;
  * a strike's FAST_SELLING place-order is won-cart eligible (the ticket loop);
  * the held / dirty paths keep the line only in a NEW stock window.

Run: python tests/test_held_line_strike.py   (listed in tests/run_offline_suite.py)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TARGET_API_PLACE_ORDER", "true")
_SCRUB = ("TARGET_FASTLANE_STAGE_TRACK", "TARGET_FASTLANE_QTY_GUARD",
          "TARGET_FASTLANE_T_STAMPS", "TARGET_FASTLANE_LOG_CART_QTY",
          "TARGET_WONCART_DIRECT", "TARGET_HELD_CART_REENTRY",
          "TARGET_AMBIGUOUS_COMMIT_LATCH", "TARGET_FASTLANE_PRE_RETRY",
          "TARGET_HELD_LINE_FLIP_STRIKE", "TARGET_HELD_LINE_FLIP_STRIKE_IDENTS",
          "TARGET_WONCART_RF_ENTRY", "TARGET_HELD_CART_TTL_S")
for _k in _SCRUB:
    os.environ.pop(_k, None)

import src.session.purchase_executor as pe  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

T = "1010892076"
OTHER = "1010892067"
MAXP_BODY = json.dumps({"message": "Items cannot be added to cart as max purchase limit exceeded",
                        "code": "MAX_PURCHASE_LIMIT_EXCEEDED"})
STRIKE_ON = {"TARGET_HELD_LINE_FLIP_STRIKE": "1", "TARGET_FASTLANE_QTY_GUARD": "1",
             "TARGET_HELD_CART_REENTRY": "1"}   # v10: the strike requires re-entry (bat:1173 arms it)

PASSED: list = []
FAILED: list = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


class _Env:
    """Temporarily set / unset environment variables."""

    def __init__(self, env):
        self.env = env
        self.saved = {}

    def __enter__(self):
        for k in set(_SCRUB) | set(self.env):
            self.saved[k] = os.environ.get(k)
            os.environ.pop(k, None)
        os.environ.update(self.env)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _SM:
    def __init__(self, ident):
        self.account_id = ident

    def _load_account_cvv(self):
        return ''


class _Tab:
    def __init__(self):
        self.js = None

    async def evaluate(self, js, await_promise=True):
        self.js = js
        return {"atc": {"status": 401}, "pre": {}, "po": {}, "skip": "atc_401"}


def _bare(ident="alt-1"):
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._fastlane_placed = False
    ex._cvv_required = False
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    ex._fast_selling_until = 0.0
    ex._persist_cvv_challenge_flag = lambda: None
    ex.session_manager = _SM(ident)
    return ex


def render(env, ident="alt-1", ordered=()):
    with _Env(env):
        ex = _bare(ident)
        for t in ordered:
            ex._note_ordered_tcin(t)
        tab = _Tab()
        asyncio.run(ex._api_fast_lane(tab, T, 2, json.dumps({"X-GyJwza5Z-a": "tok"})))
        return tab.js


NODE = shutil.which("node")
HARNESS = r"""
const scenario = %s;
const calls = [];
globalThis.fetch = async (url, opts) => {
  calls.push(String(url).split('?')[0].split('/').pop());
  let rule = null;
  for (const r of scenario) { if (String(url).includes(r.match)) { rule = r; break; } }
  if (!rule) throw new Error('no stub rule for ' + url);
  return { status: rule.status, text: async () => rule.body || '' };
};
(async () => {
  const out = await (%s);
  console.log(JSON.stringify({out, calls}));
})().catch(e => { console.log(JSON.stringify({error: String(e)})); });
"""


def run_node(js, scenario):
    src = HARNESS % (json.dumps(scenario), js)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        p = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise AssertionError(f"node failed: {p.stderr[:400]}")
        r = json.loads(p.stdout.strip().splitlines()[-1])
        if "error" in r:
            raise AssertionError("harness error: " + r["error"][:400])
        return r
    finally:
        os.unlink(path)


def _pre(tcins, qty=2):
    return json.dumps({"cart_items": [{"tcin": t, "quantity": qty} for t in tcins],
                       "payment_instructions": [{"payment_instruction_id": "PI-1",
                                                 "payment_type": "CARD"}]})


PO_OK = json.dumps({"orders": [{"order_id": "OID-1"}]})


def scen(atc_status, atc_body, pre_status=201, pre_body=None, po_status=200, po_body=PO_OK):
    return [{"match": "cart_items", "status": atc_status, "body": atc_body},
            {"match": "pre_checkout", "status": pre_status, "body": pre_body or _pre([T])},
            {"match": "v1/checkout", "status": po_status, "body": po_body}]


# ── 1. pure helpers ──────────────────────────────────────────────────────────
def test_flag_parsing():
    f = pe.held_line_strike_on
    check("flag_default_off", f("alt-1", {}) is False)
    check("flag_on_all_accounts", f("primary", {"TARGET_HELD_LINE_FLIP_STRIKE": "1"}) is True)
    env = {"TARGET_HELD_LINE_FLIP_STRIKE": "1", "TARGET_HELD_LINE_FLIP_STRIKE_IDENTS": "alt-1; Business"}
    check("flag_scope_listed", f("alt-1", env) and f("business", env))
    check("flag_scope_unlisted", f("primary", env) is False)
    check("flag_other_values_off", f("alt-1", {"TARGET_HELD_LINE_FLIP_STRIKE": "true"}) is False)


def test_window_fresh():
    w = pe.strike_window_fresh
    check("fresh_new_window", w({"live": True, "window_start": 200.0}, 100.0) is True)
    check("fresh_same_window", w({"live": True, "window_start": 100.0}, 150.0) is False)
    check("fresh_not_live", w({"live": False, "window_start": 200.0}, 100.0) is False)
    check("fresh_unknown_live", w({"live": None, "window_start": 200.0}, 100.0) is False)
    check("fresh_no_window", w({"live": True, "window_start": 0.0}, 100.0) is False)
    check("fresh_no_line_ts", w({"live": True, "window_start": 200.0}, None) is False)
    check("fresh_bad_input", w(None, 1.0) is False and w({"live": True, "window_start": "x"}, 1) is False)


def test_legacy_reason():
    r = pe.strike_legacy_400_reason
    check("reason_no_strike_is_hold", r({"atc": {"status": 400}}) == "held_line_strike_hold")
    check("reason_none_is_hold", r(None) == "held_line_strike_hold")
    check("reason_no_line", r({"atc": {"strike": True}, "skip": "strike_no_line"}) == "held_line_strike_no_line")
    check("reason_po", r({"atc": {"strike": True}, "po": {"fired": True, "status": 424}})
          == "held_line_strike_po_424")
    check("reason_pre", r({"atc": {"strike": True}, "skip": "pre_401"}) == "held_line_strike_pre_401")
    toks = ('oos', 'out_of_stock', 'sold_out', 'reservation', 'unavailable')
    rs = [r({"atc": {"strike": True}, "skip": s}) for s in ("strike_no_line", "pre_429", "foreign_cart_item",
                                                           "cart_qty_over", "")]
    check("reason_has_no_bpm_terminal_token", not any(t in x for x in rs for t in toks), str(rs))


def test_eligible():
    fs = {"fired": True, "status": 429, "body": "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"}
    check("eligible_strike_fs_po", pe.woncart_eligible({"atc": {"status": 400, "strike": True}, "po": fs}))
    check("eligible_strike_pre_stop", pe.woncart_eligible(
        {"atc": {"status": 400, "strike": True}, "po": {"fired": False}, "skip": "pre_429"}))
    check("not_eligible_plain_400", not pe.woncart_eligible({"atc": {"status": 400}, "po": fs}))
    check("not_eligible_strike_truthy_not_true",
          not pe.woncart_eligible({"atc": {"status": 400, "strike": 1}, "po": fs}))


# ── 2. the rendered JS ───────────────────────────────────────────────────────
def test_js_off_is_byte_identical():
    base = render({})
    check("js_flag_off_identical", render({"TARGET_HELD_LINE_FLIP_STRIKE": "0"}) == base)
    check("js_qg_only_has_no_strike", "out.atc.strike" not in render({"TARGET_FASTLANE_QTY_GUARD": "1"}))
    check("js_strike_without_qg_has_no_strike",
          "out.atc.strike" not in render({"TARGET_HELD_LINE_FLIP_STRIKE": "1"}))
    check("js_unlisted_account_has_no_strike", "out.atc.strike" not in render(
        dict(STRIKE_ON, TARGET_HELD_LINE_FLIP_STRIKE_IDENTS="business"), ident="alt-1"))
    check("js_ordered_tcin_has_no_strike", "out.atc.strike" not in render(STRIKE_ON, ordered=(T,)))
    check("js_on_has_strike", "out.atc.strike = true" in render(STRIKE_ON))


def test_js_scenarios():
    if not NODE:
        check("node_available", False, "node not on PATH")
        return
    js = render(STRIKE_ON)
    r = run_node(js, scen(400, MAXP_BODY))
    check("strike_places_order_on_held_line", r["calls"] == ["cart_items", "pre_checkout", "checkout"]
          and r["out"]["po"]["status"] == 200 and r["out"]["atc"]["strike"] is True, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_body=_pre([T, OTHER])))
    check("strike_foreign_line_blocks_po", r["calls"] == ["cart_items", "pre_checkout"]
          and r["out"]["skip"] == "foreign_cart_item" and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_body=_pre([])))
    check("strike_no_line_blocks_po", r["out"]["skip"] == "strike_no_line"
          and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_body=_pre([OTHER])))
    # v10: a line that is not ours is foreign BEFORE "no line of ours" is concluded.
    check("strike_only_other_line_blocks_po", r["out"]["skip"] == "foreign_cart_item"
          and r["out"]["po"]["fired"] is False, str(r))
    # v10 (verifier #9, 3b): a 2xx pre_checkout with no cart_items array keeps the line.
    r = run_node(js, scen(400, MAXP_BODY, pre_body=json.dumps({"cart_id": "C1"})))
    check("v10_strike_pre_without_array_keeps_line", r["out"]["skip"] == "strike_qty_unknown"
          and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_body=json.dumps(
        {"cart_items": [{"item": {"tcin": T}, "quantity": 2}]})))
    check("v10_strike_nested_tcin_is_foreign", r["out"]["skip"] == "foreign_cart_item"
          and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_body=_pre([T], qty=4)))
    check("strike_qty_over_blocks_po", r["out"]["skip"] == "cart_qty_over"
          and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_status=429, pre_body="FAST_SELLING"))
    check("strike_pre_429_stops_before_po", r["out"]["skip"] == "pre_429"
          and r["out"]["po"]["fired"] is False, str(r))
    # Verifier (10-01) counter-cases: fail closed, not open.
    null_tcin = json.dumps({"cart_items": [{"tcin": T, "quantity": 2}, {"tcin": None, "quantity": 1}]})
    r = run_node(js, scen(400, MAXP_BODY, pre_body=null_tcin))
    check("strike_null_tcin_line_is_foreign", r["out"]["skip"] == "foreign_cart_item"
          and r["out"]["po"]["fired"] is False, str(r))
    no_qty = json.dumps({"cart_items": [{"tcin": T}]})
    r = run_node(js, scen(400, MAXP_BODY, pre_body=no_qty))
    check("strike_unreadable_qty_blocks_po", r["out"]["skip"] == "strike_qty_unknown"
          and r["out"]["po"]["fired"] is False, str(r))
    zero_qty = json.dumps({"cart_items": [{"tcin": T, "quantity": 0}]})
    r = run_node(js, scen(400, MAXP_BODY, pre_body=zero_qty))
    check("strike_zero_qty_blocks_po", r["out"]["skip"] == "strike_qty_unknown"
          and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, json.dumps({"code": "INVALID_REQUEST"})))
    check("other_400_never_continues", r["calls"] == ["cart_items"] and r["out"]["skip"] == "atc_400"
          and not r["out"]["atc"].get("strike"), str(r))
    r = run_node(js, scen(401, '{"errorKey":"_ERR_AUTH_DENIED"}'))
    check("strike_on_401_unchanged", r["calls"] == ["cart_items"] and r["out"]["skip"] == "atc_401", str(r))
    r = run_node(js, scen(201, json.dumps({"tcin": T, "cart_item_id": "CI", "quantity": 2})))
    check("strike_on_normal_201_unchanged", r["calls"] == ["cart_items", "pre_checkout", "checkout"]
          and not r["out"]["atc"].get("strike"), str(r))
    r = run_node(render({}), scen(400, MAXP_BODY))
    check("flag_off_maxp_stops_like_today", r["calls"] == ["cart_items"] and r["out"]["skip"] == "atc_400", str(r))


# ── 3. Python paths ──────────────────────────────────────────────────────────
def test_placed_records_order_and_disables_strike():
    with _Env(STRIKE_ON):
        ex = _bare()
        check("strike_active_before_order", ex._held_line_strike_active(T))
        v, _ = ex._apply_fast_lane_result(
            {"atc": {"status": 400, "strike": True}, "po": {"fired": True, "status": 200, "body": PO_OK}},
            T, time.time())
        check("placed_verdict", v == "placed", v)
        check("strike_inactive_after_order", not ex._held_line_strike_active(T))
        check("other_tcin_still_active", ex._held_line_strike_active(OTHER))


def test_placed_strike_returns_before_legacy_branches():
    """Structural pin: the placed-strike success return sits right after the
    terminal return, ahead of the legacy ATC branches (whose MAX_PURCHASE
    self-heal would clear the cart and add again after a committed order)."""
    src = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")
    i = src.find("_verdict, _terminal = self._apply_fast_lane_result(_fl, tcin, start_time)")
    j = src.find("if _verdict == 'placed' and atc_result.get('strike') is True:", i)
    k = src.find("_v2, _t2 = await self._won_cart_ticket_loop(", i)
    h = src.find("elif atc_status == 400 and 'EXCEEDED' in atc_body.upper():")
    check("placed_strike_guard_present", i > 0 and j > i, f"i={i} j={j}")
    check("placed_strike_guard_before_loop_and_selfheal", 0 < j < k < h, f"j={j} k={k} h={h}")


STRIKE_LOOP_ON = dict(STRIKE_ON, TARGET_WONCART_DIRECT="1", TARGET_HELD_CART_REENTRY="1")
CVV_UNKNOWN_FL0 = {"stage": "cvv"}      # tracking object without cvv_put = "unknown" -> no more PUTs


def _strike_ex(window_start, now, verdict="done", loop_sets_marker=False):
    ex = _bare()
    ex._deletes = []
    ex._loops = []

    async def _loop(tab, tcin, qty, fl0, start_time, entry="first"):
        ex._loops.append((str(tcin), qty, fl0, entry))
        if loop_sets_marker:
            ex._held_cart = {"tcin": str(tcin), "created": time.time(), "new": True}
        if verdict == "placed":
            return "placed", None
        return verdict, {"success": False, "tcin": str(tcin), "reason": f"loop_{verdict}"}

    async def _success(tab, tcin, qty, start_time):
        return {"success": True, "tcin": str(tcin), "reason": "order_confirmed", "quantity": qty}

    ex._won_cart_ticket_loop = _loop
    ex._fastlane_success_result = _success

    async def _del(tab, only_tcin=None, keep_tcin=None, budget_s=15.0, abort_fn=None):
        ex._deletes.append(only_tcin or keep_tcin)
        return True, 1

    async def _rel(tab, h, why):
        ex._deletes.append(f"release:{why}")
        ex._held_cart = None
        return True

    ex._delete_cart_items = _del
    ex._held_cart_release = _rel
    ex._stock_state = lambda t: {"live": True, "window_start": window_start}
    ex._woncart_armed = lambda: True
    ex._woncart_api_path_on = lambda: True
    ex._held_desc = lambda h: "-"
    return ex


def _dirty(ex, now, tcin=T):
    d = {"tcin": tcin, "ts": now - 900, "why": "cart_ticket_cap"}
    ex._woncart_dirty = d
    return d


def test_dirty_release_strikes_through_the_loop():
    """v2 (verifier 10-01 REFUTED v1): a kept line is struck through the ticket
    loop. The keep must never return None (= go on to an add-to-cart, whose
    401 / 429 would fall into the legacy checkout that buys the whole cart)."""
    now = time.time()
    with _Env(STRIKE_LOOP_ON):
        ex = _strike_ex(window_start=now - 10, now=now, verdict="done")
        d = _dirty(ex, now)
        r = asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_keep_never_returns_none", r is not None, str(r))
        check("dirty_keep_runs_loop_entry_strike", ex._loops == [(T, 2, CVV_UNKNOWN_FL0, "strike")], str(ex._loops))
        check("dirty_keep_no_delete", ex._deletes == [], str(ex._deletes))
        check("dirty_keep_returns_loop_result", r == {"success": False, "tcin": T, "reason": "loop_done"}, str(r))
        check("dirty_keep_done_clears_old_flag", ex._woncart_dirty is None)

        ex = _strike_ex(window_start=now - 10, now=now, verdict="terminal")
        d = _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_wrapper_leaves_flag_on_terminal", ex._woncart_dirty is d)

        ex = _strike_ex(window_start=now - 10, now=now, verdict="placed")
        _dirty(ex, now)
        r = asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_keep_placed_returns_success", isinstance(r, dict) and r.get("success") is True, str(r))
        check("dirty_keep_placed_clears_flag", ex._woncart_dirty is None)

        ex = _strike_ex(window_start=now - 1000, now=now)      # same window as the exit
        _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_same_window_deletes_like_today", ex._deletes == [T] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")

        ex = _strike_ex(window_start=now - 10, now=now)        # left line is another TCIN
        _dirty(ex, now, OTHER)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_other_tcin_deletes_like_today", ex._deletes == [OTHER] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")

        ex = _strike_ex(window_start=now - 10, now=now)        # caller without a quantity
        _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now))
        check("dirty_no_quantity_deletes_like_today", ex._deletes == [T] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")

        ex = _strike_ex(window_start=now - 10, now=now)        # loop cannot run
        ex._woncart_api_path_on = lambda: False
        _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_loop_not_ok_deletes_like_today", ex._deletes == [T] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")
        ex = _strike_ex(window_start=now - 10, now=now)        # CVV state carried over
        d = _dirty(ex, now)
        d["cvv_put"] = "ok"
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_keep_carries_cvv_ok", ex._loops == [(T, 2, {"cvv": {"put": 200}}, "strike")], str(ex._loops))
        ex = _strike_ex(window_start=now - 10, now=now)
        d = _dirty(ex, now)
        d["cvv_put"] = "none"
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_keep_carries_cvv_none", ex._loops == [(T, 2, None, "strike")], str(ex._loops))
    with _Env(dict(STRIKE_LOOP_ON, TARGET_HELD_CART_REENTRY="0")):   # WC-3 off: no strike
        ex = _strike_ex(window_start=now - 10, now=now)
        _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_wc3_off_deletes_like_today", ex._deletes == [T] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")
    with _Env(STRIKE_ON):                                       # WONCART_DIRECT off
        ex = _strike_ex(window_start=now - 10, now=now)
        _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_woncart_off_deletes_like_today", ex._deletes == [T] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")
    with _Env({"TARGET_WONCART_DIRECT": "1"}):                  # strike flag off
        ex = _strike_ex(window_start=now - 10, now=now)
        _dirty(ex, now)
        asyncio.run(ex._woncart_dirty_release(None, T, now, 2))
        check("dirty_flag_off_deletes_like_today", ex._deletes == [T] and ex._loops == [],
              f"{ex._deletes} {ex._loops}")


def _held(now, tcin=T):
    return {"tcin": tcin, "created": now - 1000, "first_201_ts": now - 1000,
            "last_ticket_ts": now - 950, "tickets": 3}


def test_held_entry_strikes_through_the_loop():
    now = time.time()
    env = dict(STRIKE_LOOP_ON, TARGET_HELD_CART_TTL_S="60")
    with _Env(env):
        ex = _strike_ex(window_start=now - 10, now=now, verdict="done")
        h = _held(now)
        ex._held_cart = h
        r = asyncio.run(ex._held_cart_entry(None, T, 2, now))
        check("held_keep_never_returns_none", r is not None, str(r))
        check("held_keep_runs_loop_entry_strike", ex._loops == [(T, 2, CVV_UNKNOWN_FL0, "strike")], str(ex._loops))
        check("held_keep_no_release", ex._deletes == [], str(ex._deletes))
        check("held_keep_done_clears_old_marker", ex._held_cart is None)

        ex = _strike_ex(window_start=now - 10, now=now, verdict="done", loop_sets_marker=True)
        ex._held_cart = _held(now)
        asyncio.run(ex._held_cart_entry(None, T, 2, now))
        check("held_keep_loop_marker_survives", isinstance(ex._held_cart, dict)
              and ex._held_cart.get("new") is True, str(ex._held_cart))

        ex = _strike_ex(window_start=now - 10, now=now, verdict="terminal")
        h = _held(now)
        ex._held_cart = h
        asyncio.run(ex._held_cart_entry(None, T, 2, now))
        check("held_wrapper_leaves_marker_on_terminal", ex._held_cart is h)

        ex = _strike_ex(window_start=now - 960, now=now)       # opened before the last ticket
        ex._held_cart = _held(now)
        asyncio.run(ex._held_cart_entry(None, T, 2, now))
        check("held_ttl_same_window_retires_like_today",
              ex._deletes == ["release:retired_ttl"] and ex._loops == [], f"{ex._deletes} {ex._loops}")

        ex = _strike_ex(window_start=now - 10, now=now)        # marker for another TCIN
        ex._held_cart = _held(now, OTHER)
        asyncio.run(ex._held_cart_entry(None, T, 2, now))
        check("held_other_tcin_retires_like_today",
              ex._deletes == ["release:retired_ttl"] and ex._loops == [], f"{ex._deletes} {ex._loops}")
    with _Env({"TARGET_WONCART_DIRECT": "1", "TARGET_HELD_CART_TTL_S": "60"}):   # strike off
        ex = _strike_ex(window_start=now - 10, now=now)
        ex._held_cart = _held(now)
        asyncio.run(ex._held_cart_entry(None, T, 2, now))
        check("held_flag_off_retires_like_today",
              ex._deletes == ["release:retired_ttl"] and ex._loops == [], f"{ex._deletes} {ex._loops}")


def test_ledger_marks_strike_source():
    ex = _bare()
    L = ex._woncart_new_ledger(T, 2, {"atc": {"status": 400, "strike": True},
                                      "pre": {"status": 201, "tcins": [T], "qty": 2}}, time.time())
    check("ledger_strike_src", L.get("first_201_src") == "strike_400", str(L.get("first_201_src")))
    check("ledger_strike_verified_on_clean_pre", L.get("verified") is True, str(L.get("verified")))
    L = ex._woncart_new_ledger(T, 2, {"atc": {"status": 201}}, time.time())
    check("ledger_normal_src_unchanged", L.get("first_201_src") == "loop_entry", str(L.get("first_201_src")))


def test_kept_line_predicate():
    k = pe.strike_kept_line
    check("kept_qty_unknown", k({"atc": {"strike": True}, "skip": "strike_qty_unknown"}) is True)
    check("kept_po_rejected", k({"atc": {"strike": True}, "skip": "", "po": {"fired": True, "status": 424}}))
    check("not_kept_no_line", k({"atc": {"strike": True}, "skip": "strike_no_line"}) is False)
    check("not_kept_pre_fail", k({"atc": {"strike": True}, "skip": "pre_429", "po": {"fired": False}}) is False)
    check("not_kept_no_strike", k({"atc": {"status": 400}, "skip": "atc_400"}) is False)
    check("not_kept_none", k(None) is False)


def test_js_strict_qty():
    if not NODE:
        check("node_available_qty", False, "node not on PATH")
        return
    js = render(STRIKE_ON)
    two_lines = json.dumps({"cart_items": [{"tcin": T, "quantity": 2}, {"tcin": T, "quantity": None}]})
    r = run_node(js, scen(400, MAXP_BODY, pre_body=two_lines))
    check("strict_qty_null_second_line_blocks", r["out"]["skip"] == "strike_qty_unknown"
          and r["out"]["po"]["fired"] is False, str(r))
    for label, q in (("string", "2"), ("bool", True), ("list", [2])):
        body = json.dumps({"cart_items": [{"tcin": T, "quantity": q}]})
        r = run_node(js, scen(400, MAXP_BODY, pre_body=body))
        check(f"strict_qty_{label}_blocks", r["out"]["skip"] == "strike_qty_unknown"
              and r["out"]["po"]["fired"] is False, str(r))
    r = run_node(js, scen(400, MAXP_BODY, pre_body=_pre([T], qty=3)))
    check("strict_qty_over_q_blocks", r["out"]["skip"] == "cart_qty_over"
          and r["out"]["po"]["fired"] is False, str(r))


# ── 4. real executor paths (the won-cart smoke harness: stub tabs, no browser) ──
# Verifier 10-02 reproduced v2's hole against the REAL _execute_purchase_impl: a
# line the strike kept untracked was bought whole by the legacy silent-hold
# checkout on the next 401 / 429. These pin that it can no longer happen.
sys.path.insert(0, str(ROOT / "tests"))
import test_won_cart_direct_smoke as S  # noqa: E402  (import only; its main() does not run)

ST = S.TCIN
SO = S.OTHER
S_ENV = dict(S.ARMED, TARGET_HELD_CART_REENTRY="1", TARGET_HELD_LINE_FLIP_STRIKE="1",
             TARGET_API_PLACE_ORDER="true")
STRIKE_QU = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
             "pre": {"status": 201, "n": 1, "tcins": [ST], "qty": None, "pi": []},
             "po": {"status": 0, "body": "", "fired": False}, "skip": "strike_qty_unknown"}
STRIKE_PO424 = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
                "pre": {"status": 201, "n": 1, "tcins": [ST], "qty": 2, "pi": []},
                "po": {"status": 424, "body": "{}", "fired": True}, "skip": ""}
STRIKE_NOLINE = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
                 "pre": {"status": 201, "n": 0, "tcins": [], "qty": 0, "pi": []},
                 "po": {"status": 0, "body": "", "fired": False}, "skip": "strike_no_line"}
ATC401 = {"atc": {"status": 401, "body": '{"errorKey":"_ERR_AUTH_DENIED"}', "cart_items": []},
          "pre": {"status": 0}, "po": {"status": 0, "body": "", "fired": False}, "skip": "atc_401"}
ATC429 = {"atc": {"status": 429, "body": '{"message":"FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"}',
                  "cart_items": []},
          "pre": {"status": 0}, "po": {"status": 0, "body": "", "fired": False}, "skip": "atc_429"}


def _impl(fl_script, cart, legacy_atc=None):
    """Real _execute_purchase_impl with a cart MODEL: a delete removes lines, and the
    silent-hold read and the legacy add see the current cart."""
    c = S.Clock(time.time())
    ex, tab = S.impl_ex(c, {"atc": {"status": 0}})
    for k, v in dict(_cart_hold_check_on=True, _cart_hold_check_interval_s=1.0,
                     _last_cart_hold_check_ts=0.0, _cart_hold_verbose=True, _cart_hold_skip_edge=True,
                     _atc_401_ladder_on=False, _tcin_throttle_cooldown_s=90.0).items():
        setattr(ex, k, v)
    ws0 = time.time() - 3                     # one window, opened before dispatch 1
    ex._stock_live_fn = lambda t: {"live": True, "window_start": ws0}
    ex.cart = list(cart)
    fls = list(fl_script)

    async def _fl(t, tcin, qty, hdrs):
        ex.fl_calls.append((tcin, qty))
        return json.loads(json.dumps(fls.pop(0)))

    ex._api_fast_lane = _fl

    async def _del(t, only_tcin=None, keep_tcin=None, budget_s=15.0, ids=None, abort_fn=None):
        ex.deletes.append({"only": only_tcin, "keep": keep_tcin})
        if only_tcin is not None:
            ex.cart = [x for x in ex.cart if x != str(only_tcin)]
        elif keep_tcin is not None:
            ex.cart = [x for x in ex.cart if x == str(keep_tcin)]
        else:
            ex.cart = []
        return True, 1

    ex._delete_cart_items = _del
    ex.clears = []

    async def _clear(t):
        ex.clears.append(1)
        ex.cart = []
        return True

    ex._clear_cart = _clear
    la = list(legacy_atc or [])

    async def ev(js, await_promise=False, **kw):
        if "web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART,CART_ITEMS" in js and "tcins" in js:
            return {"ok": True, "tcins": list(ex.cart)}
        if "web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY" in js and "method: 'POST'" in js:
            r = la.pop(0)
            if r.get("status") in (200, 201):
                ex.cart.append(str((r.get("cart_items") or [{}])[0].get("tcin") or ""))
            return r
        return None

    tab.evaluate = ev
    return ex, tab


def _go(ex, tcin, extra=None):
    with S.in_tmp_cwd(), S.env(**dict(S_ENV, **(extra or {}))):
        try:
            return S.run(ex._execute_purchase_impl(tcin, quantity=2))
        except S.LegacyReached:
            return {"reason": "LEGACY_CHECKOUT_REACHED"}


def test_real_kept_line_never_bought_by_legacy():
    for label, first, second in (("qty_unknown_then_401", STRIKE_QU, ATC401),
                                 ("po424_then_401", STRIKE_PO424, ATC401),
                                 ("po424_then_429", STRIKE_PO424, ATC429)):
        ex, _ = _impl([first, second], cart=[ST])
        r1 = _go(ex, ST)
        d = getattr(ex, "_woncart_dirty", None)
        check(f"real_{label}_strike_leaves_line_flagged", isinstance(d, dict) and d.get("tcin") == ST
              and str(r1.get("reason", "")).startswith("held_line_strike_") and not ex.clears,
              f"r1={r1} dirty={d} clears={ex.clears}")
        r2 = _go(ex, ST)
        check(f"real_{label}_next_shot_never_reaches_legacy_checkout",
              "checking_out" not in ex.statuses and r2.get("reason") != "LEGACY_CHECKOUT_REACHED",
              f"r2={r2} statuses={ex.statuses}")
        check(f"real_{label}_line_deleted_before_the_shot",
              ex.deletes and ex.deletes[0]["only"] == ST and ST not in ex.cart,
              f"deletes={ex.deletes} cart={ex.cart}")
    # Route 2: legacy-only add (FAST_SELLING cooldown, no strike ran) -> today's self-heal.
    ex, _ = _impl([], cart=[ST, SO], legacy_atc=[{"status": 400, "body": MAXP_BODY, "cart_items": []},
                                                 {"status": 400, "body": MAXP_BODY},
                                                 {"status": 400, "body": MAXP_BODY}])
    ex._fast_selling_until = time.time() + 300
    r = _go(ex, ST)
    check("real_legacy_only_400_runs_self_heal", len(ex.clears) == 1
          and getattr(ex, "_woncart_dirty", None) is None, f"r={r} clears={ex.clears}")
    # strike found no line of ours: today's self-heal.
    ex, _ = _impl([STRIKE_NOLINE], cart=[SO], legacy_atc=[{"status": 400, "body": MAXP_BODY},
                                                          {"status": 400, "body": MAXP_BODY}])
    r = _go(ex, ST)
    check("real_strike_no_line_runs_self_heal", len(ex.clears) == 1, f"r={r} clears={ex.clears}")


def _held_full(c, **kw):
    h = {"tcin": ST, "qty": 2, "cart_id": "", "created": c.t - 1000, "first_201_ts": c.t - 1000,
         "first_201_src": "atc_t1", "tickets": 3, "sched_used": 6, "verified": False, "pi_id": "",
         "cvv_put": "none", "last_ticket_ts": c.t - 950, "fs_seen_ts": 0.0, "source": "first"}
    h.update(kw)
    return h


def _loop_env(**extra):
    return dict(S_ENV, TARGET_HELD_CART_TTL_S="60", **extra)


def test_real_loop_terminal_keeps_line_tracked():
    c = S.Clock()
    ex = S.bare(c)
    ex._stock_live_fn = lambda t: {"live": True, "window_start": c.t - 10}
    ex._held_cart = _held_full(c)
    tab = S.TicketTab(ex, [S.t_prepo(0)], c)
    with S.in_tmp_cwd(), S.env(**_loop_env()), S.fake_time(c):
        r = S.run(ex._held_cart_entry(tab, ST, 2, c.t))
    tracked = ((isinstance(ex._held_cart, dict) and ex._held_cart.get("tcin") == ST)
               or (isinstance(getattr(ex, "_woncart_dirty", None), dict) and ex._woncart_dirty.get("tcin") == ST))
    check("real_held_strike_terminal_line_tracked", tracked and bool(r.get("ambiguous_commit")),
          f"r={r} held={ex._held_cart} dirty={getattr(ex, '_woncart_dirty', None)}")

    c = S.Clock()
    ex = S.bare(c)
    ex._stock_live_fn = lambda t: {"live": True, "window_start": c.t - 10}
    ex._woncart_dirty = {"tcin": ST, "ts": c.t - 900, "why": "cart_ticket_cap", "cvv_put": "none"}
    tab = S.TicketTab(ex, [S.t_prepo(0)], c)
    with S.in_tmp_cwd(), S.env(**_loop_env()), S.fake_time(c):
        S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
    d = getattr(ex, "_woncart_dirty", None)
    check("real_dirty_strike_terminal_line_tracked", isinstance(d, dict) and d.get("tcin") == ST, str(d))

    c = S.Clock()
    ex = S.bare(c)
    ex._stock_live_fn = lambda t: {"live": True, "window_start": c.t - 10}
    d0 = {"tcin": ST, "ts": c.t - 900, "why": "cart_ticket_cap", "cvv_put": "none"}
    ex._woncart_dirty = d0
    tab = S.TicketTab(ex, [S.t_prepo(429, S.FS_BODY, rej=(429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION")),
                           S.t_po(200, S.ORDER_BODY)], c)
    with S.in_tmp_cwd(), S.env(**_loop_env()), S.fake_time(c):
        r = S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
    check("real_dirty_strike_places_order", isinstance(r, dict) and r.get("success") is True
          and ST in (getattr(ex, "_ordered_tcins", None) or set()), f"r={r}")
    check("real_dirty_strike_first_ticket_strict", tab.tickets and tab.tickets[0][1] == "pre_po",
          str([m for _, m, _ in tab.tickets]))
    check("real_dirty_strike_placed_drops_old_flag", getattr(ex, "_woncart_dirty", None) is not d0)


def test_real_cvv_state_carried():
    for state, expect_first in (("ok", False), ("none", True)):
        c = S.Clock()
        ex = S.bare(c, cvv="123", cvv_required=True)
        ex._stock_live_fn = lambda t: {"live": True, "window_start": c.t - 10}
        ex._held_cart = _held_full(c, cvv_put=state)
        tab = S.TicketTab(ex, [S.t_pre(429, S.FS_BODY)] * 3, c)
        with S.in_tmp_cwd(), S.env(**_loop_env()), S.fake_time(c, stop_after=3):
            try:
                S.run(ex._held_cart_entry(tab, ST, 2, c.t))
            except BaseException:  # noqa: BLE001 (fake_time stop)
                pass
        first = bool(tab.tickets) and ("const CVV_FIRST = true" in tab.tickets[0][2])
        check(f"real_cvv_{state}_first_ticket_cvv_first={expect_first}", bool(tab.tickets) and first == expect_first,
              f"tickets={len(tab.tickets)} first={first}")


def test_real_strike_timeout_flags_line():
    for stage in ("pre", "cvv"):
        c = S.Clock(time.time())
        ex = S.bare(c)

        class _Tab:
            async def evaluate(self, js, await_promise=False, **kw):
                if "e.abort = true" in js:
                    return {"s": stage, "aborted": True, "atc": 400}
                raise asyncio.TimeoutError()

        with S.in_tmp_cwd(), S.env(**dict(S_ENV, TARGET_FASTLANE_STAGE_TRACK="1")):
            fl = S.run(ex._api_fast_lane(_Tab(), ST, 2, json.dumps({"x": "y"})))
        d = getattr(ex, "_woncart_dirty", None)
        check(f"real_strike_timeout_{stage}_flags_line", isinstance(d, dict) and d.get("tcin") == ST
              and fl.get("po", {}).get("fired") is True, f"fl={fl} dirty={d}")
    c = S.Clock(time.time())
    ex = S.bare(c)

    class _Tab2:
        async def evaluate(self, js, await_promise=False, **kw):
            if "e.abort = true" in js:
                return {"s": "pre", "aborted": True, "atc": 400}
            raise asyncio.TimeoutError()

    with S.in_tmp_cwd(), S.env(**dict(S.ARMED, TARGET_HELD_CART_REENTRY="1", TARGET_API_PLACE_ORDER="true",
                                       TARGET_FASTLANE_STAGE_TRACK="1")):
        S.run(ex._api_fast_lane(_Tab2(), ST, 2, json.dumps({"x": "y"})))
    check("real_timeout_flag_off_no_flag", getattr(ex, "_woncart_dirty", None) is None)


# ── 5. v4: the three untracked-line paths the 10-02 verifier reproduced (claim 3) ──
# R-A a terminal (0 / 408 / 5xx) strike place-order and a non-dict evaluate result;
# R-B a presumed (unread) eviction inside a strike loop; R-C a failed release of a
# held marker the strike produced. Each must leave our line TRACKED; flag off = HEAD.
EVP = dict(TARGET_WONCART_EVICTION_READ="1", TARGET_WONCART_EVICTION_PRESUME="1")
CART_ST = [{"id": "CI-1234567890", "tcin": ST, "qty": 2}]
CMP_424 = '{"errors":[{"error_key":"CART_COMPARISION_FAILURE_ERROR"}]}'


def _strike_po(status, body=""):
    return {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
            "pre": {"status": 201, "n": 1, "tcins": [ST], "qty": 2, "pi": []},
            "po": {"status": status, "body": body, "fired": True}, "skip": ""}


class _Tab429(S.TicketTab):
    """Every cart GET answers 429 (the hot-window throttle); tickets as scripted."""
    async def evaluate(self, js, await_promise=False, **kw):
        if "const MODE = '" not in js and "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            return {"ok": False, "status": 429, "items": [], "has_items": False}
        return await super().evaluate(js, await_promise, **kw)


def test_v4_terminal_strike_keeps_line_tracked():
    for label, fl1 in (("po0", _strike_po(0, "TypeError: Failed to fetch")),
                       ("po503", _strike_po(503, "upstream"))):
        ex, _ = _impl([fl1, ATC401], cart=[ST])
        r1 = _go(ex, ST)
        d = getattr(ex, "_woncart_dirty", None)
        check(f"v4_RA_{label}_terminal_line_flagged", r1.get("ambiguous_commit") is True
              and isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "strike_terminal"
              and not ex.clears, f"r1={r1} dirty={d} clears={ex.clears}")
        r2 = _go(ex, ST)                     # same identity + TCIN once the AC-1 latch expired
        check(f"v4_RA_{label}_next_shot_never_reaches_legacy_checkout",
              "checking_out" not in ex.statuses and r2.get("reason") != "LEGACY_CHECKOUT_REACHED"
              and ST not in ex.cart, f"r2={r2} statuses={ex.statuses} cart={ex.cart}")
        ex, _ = _impl([fl1, dict(ATC401)], cart=[ST])
        _go(ex, ST)
        ex.cart.append(SO)                   # during the latch: another TCIN's 401'd add landed
        _go(ex, SO)
        check(f"v4_RA_{label}_other_tcin_never_buys_our_line",
              bool(ex.deletes) and ex.deletes[0]["only"] == ST and ST not in ex.cart,
              f"deletes={ex.deletes} cart={ex.cart}")

    class _NoneTab:
        async def evaluate(self, js, await_promise=False, **kw):
            return None

    for flag, want in (("1", True), ("0", False)):
        c = S.Clock(time.time())
        ex = S.bare(c)
        with S.in_tmp_cwd(), S.env(**dict(S_ENV, TARGET_HELD_LINE_FLIP_STRIKE=flag)):
            fl = S.run(ex._api_fast_lane(_NoneTab(), ST, 2, json.dumps({"x": "y"})))
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "strike_bad_result"
        check(f"v4_bad_result_flag{flag}_flagged={want}", fl.get("skip") == "bad_result" and got == want
              and (want or d is None), f"fl={fl} dirty={d}")

    # fix 4: a strike's stacked line whose delete fails stays tracked.
    qover = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
             "pre": {"status": 200, "n": 1, "tcins": [ST], "qty": 4, "pi": []},
             "po": {"status": 0, "body": "", "fired": False}, "skip": "cart_qty_over"}
    ex, _ = _impl([qover], cart=[ST])

    async def _del_fail(t, only_tcin=None, keep_tcin=None, budget_s=15.0, ids=None, abort_fn=None):
        ex.deletes.append({"only": only_tcin, "keep": keep_tcin})
        return False, 0

    ex._delete_cart_items = _del_fail
    r = _go(ex, ST)
    d = getattr(ex, "_woncart_dirty", None)
    check("v4_strike_qty_stuck_flags_line", r.get("reason") == "cart_qty_stuck" and isinstance(d, dict)
          and d.get("tcin") == ST and d.get("why") == "strike_qty_stuck", f"r={r} dirty={d}")


def test_v4_presumed_eviction_keeps_strike_line_flagged():
    for flag, tickets in (("1", 1), ("0", 0)):
        c = S.Clock()
        ex = S.bare(c)
        ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
        ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
        ex.delete_result = (False, 0)        # the cart GET is 429: any delete fails
        tab = _Tab429(ex, [S.t_prepo(424, CMP_424, rej=(424, "CART_COMPARISION_FAILURE_ERROR"))], c,
                      cart_items=[dict(x) for x in CART_ST])
        e = dict(_loop_env(), TARGET_HELD_LINE_FLIP_STRIKE=flag, TARGET_HELD_CART_TTL_S="900", **EVP)
        with S.in_tmp_cwd(), S.env(**e), S.fake_time(c):
            r = S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
        d = getattr(ex, "_woncart_dirty", None)
        check(f"v4_RB_flag{flag}_presumed_eviction_line_still_flagged",
              isinstance(d, dict) and d.get("tcin") == ST and len(tab.tickets) == tickets,
              f"r={r} dirty={d} tickets={len(tab.tickets)}")
    # Control: a normal won cart (entry='first') keeps HEAD's presume rule (no flag on all-429).
    for flag in ("1", "0"):
        c = S.Clock()
        ex = S.bare(c)
        ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
        tab = _Tab429(ex, [S.t_prepo(424, CMP_424, rej=(424, "CART_COMPARISION_FAILURE_ERROR"))], c,
                      cart_items=[dict(x) for x in CART_ST])
        (v, r), _ = S.loop_run(ex, tab, c, S.FL_PO_FS, entry="first",
                                TARGET_HELD_LINE_FLIP_STRIKE=flag, **EVP)
        check(f"v4_RB_first_entry_flag{flag}_unchanged",
              v == "done" and getattr(ex, "_woncart_dirty", None) is None,
              f"v={v} r={r} dirty={getattr(ex, '_woncart_dirty', None)}")


def test_v4_failed_release_keeps_line_tracked():
    for flag in ("1", "0"):
        c = S.Clock()
        ex = S.bare(c)
        live = {"v": True}
        ex._stock_live_fn = lambda t, c=c, live=live: {"live": live["v"], "window_start": c.t - 10}
        ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
        ex.delete_result = (False, 0)
        tab = _Tab429(ex, [S.t_prepo(401), S.t_po(401), S.t_po(401), S.t_po(401)], c,
                      cart_items=[dict(x) for x in CART_ST])
        e = dict(_loop_env(), TARGET_HELD_LINE_FLIP_STRIKE=flag, TARGET_HELD_CART_TTL_S="900")
        with S.in_tmp_cwd(), S.env(**e), S.fake_time(c):
            S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
        live["v"] = False                    # ST went out of stock; the next dispatch is SO
        with S.in_tmp_cwd(), S.env(**e), S.fake_time(c):
            r2 = (S.run(ex._woncart_dirty_release(tab, SO, c.t, 2))
                  if getattr(ex, "_woncart_dirty", None) else None)
            if r2 is None and ex._held_cart is not None:
                r2 = S.run(ex._held_cart_entry(tab, SO, 2, c.t))
        d, h = getattr(ex, "_woncart_dirty", None), ex._held_cart
        tracked = ((isinstance(d, dict) and d.get("tcin") == ST)
                   or (isinstance(h, dict) and h.get("tcin") == ST))
        check(f"v4_RC_flag{flag}_failed_release_line_tracked",
              tracked and (r2 or {}).get("reason") == "held_cart_release_failed",
              f"r2={r2} dirty={d} held={h}")
    # _held_cart_release units (strike on): a good delete sets no flag; a failed one
    # never overwrites another TCIN's dirty flag.
    c = S.Clock()
    ex = S.bare(c)
    tab = _Tab429(ex, [], c, cart_items=[])
    ex.delete_result = (True, 1)
    h = _held_full(c)
    ex._held_cart = h
    with S.in_tmp_cwd(), S.env(**_loop_env()), S.fake_time(c):
        ok = S.run(ex._held_cart_release(tab, h, "unit"))
    check("v4_release_ok_sets_no_flag", ok is True and getattr(ex, "_woncart_dirty", None) is None
          and ex._held_cart is None)
    other = {"tcin": SO, "ts": c.t, "why": "x", "cvv_put": "none"}
    ex._woncart_dirty = other
    ex.delete_result = (False, 0)
    h = _held_full(c)
    ex._held_cart = h
    with S.in_tmp_cwd(), S.env(**_loop_env()), S.fake_time(c):
        ok = S.run(ex._held_cart_release(tab, h, "unit"))
    check("v4_release_fail_never_overwrites_other_flag", ok is False and ex._woncart_dirty is other)
    ex._woncart_dirty = None
    h = _held_full(c)
    ex._held_cart = h
    with S.in_tmp_cwd(), S.env(**dict(_loop_env(), TARGET_HELD_LINE_FLIP_STRIKE="0")), S.fake_time(c):
        S.run(ex._held_cart_release(tab, h, "unit"))
    check("v4_release_fail_flag_off_unchanged", getattr(ex, "_woncart_dirty", None) is None
          and ex._held_cart is None)


# ── 6. v5: verifier #4 (10-02) paths A / B / C and the CVV ledger (claim 5) ──
# A: the in-chain strike reaches the ticket loop with entry='first'; B: the
# foreign-cart bail trusts _clear_cart's True; C: _held_cart_entry presumes a
# strike's held line gone. The world below models the cart, so a delete really
# removes a line (the smoke stub's delete does not touch the scripted cart).
ENV5 = dict(S.ARMED, TARGET_HELD_CART_REENTRY="1", TARGET_HELD_LINE_FLIP_STRIKE="1",
            TARGET_API_PLACE_ORDER="true", TARGET_HELD_CART_TTL_S="900", **EVP)
ATC401_5 = {"atc": {"status": 401, "body": '{"errorKey":"_ERR_AUTH_DENIED"}', "cart_items": []},
            "pre": {"status": 0}, "po": {"status": 0, "body": "", "fired": False}, "skip": "atc_401"}


class _World(S.TicketTab):
    """Tickets as scripted; the cart GET answers 429 while `throttled`; the
    silent-hold read, the legacy add and _delete_cart_items act on the cart model."""
    def __init__(self, ex, script, clock, cart, legacy=(), throttled=True):
        super().__init__(ex, script, clock, cart_items=[{"id": f"CI-{t}", "tcin": t, "qty": 2} for t in cart])
        self.throttled, self.legacy, self.log = throttled, list(legacy), []

    async def evaluate(self, js, await_promise=False, **kw):
        if "const MODE = '" in js:
            self.log.append("ticket")
            return await super().evaluate(js, await_promise, **kw)
        if "cart_item_id).filter(Boolean)" in js:              # _api_clear_cart's GET
            self.log.append("api_clear_get")
            return {"ok": False, "status": 429}
        if "has_items" in js:                                  # _CART_ITEMS_READ_JS
            self.log.append("cart_read")
            if self.throttled:
                return {"ok": False, "status": 429, "items": [], "has_items": False}
            return await super().evaluate(js, await_promise, **kw)
        if "filter(Boolean)" in js:                            # _check_cart_hold
            self.log.append("hold_read")
            out = {"ok": True, "tcins": [i["tcin"] for i in self.cart_items if i["tcin"]]}
            if "n: Array.isArray(d.cart_items)" in js:         # v7: rendered only with the strike on
                out["n"] = len(self.cart_items)
            return out
        if "cart_items?field_groups=CART,CART_ITEMS,SUMMARY" in js and "method: 'POST'" in js:
            return self.legacy.pop(0)
        return None

    async def select(self, sel, timeout=None):
        return None

    async def find(self, text, best_match=True, timeout=None):
        return None


def _world_ex(fl1, script, cart, fail_delete=False):
    c = S.Clock(time.time())
    ex, _ = S.impl_ex(c, fl1)
    del ex._won_cart_ticket_loop                       # the REAL loop, not the harness stub
    for k, v in dict(_cart_hold_check_on=True, _cart_hold_check_interval_s=0.0,
                     _last_cart_hold_check_ts=0.0, _cart_hold_verbose=True, _cart_hold_skip_edge=True,
                     _atc_401_ladder_on=False, _tcin_throttle_cooldown_s=90.0).items():
        setattr(ex, k, v)
    ex._stock_live_fn = lambda t: {"live": True, "window_start": c.t - 3}
    tab = _World(ex, script, c, cart, legacy=[dict(ATC401_5["atc"])] * 3)
    ex.fail_delete = fail_delete

    async def _del(t, only_tcin=None, keep_tcin=None, budget_s=15.0, ids=None, abort_fn=None):
        ex.deletes.append({"only": only_tcin, "keep": keep_tcin})
        if ex.fail_delete:
            return False, 0
        n0 = len(tab.cart_items)
        if ids is not None:
            idset = {str(x) for x in ids}
            tab.cart_items[:] = [i for i in tab.cart_items if i["id"] not in idset]
        elif only_tcin is not None:
            tab.cart_items[:] = [i for i in tab.cart_items if i["tcin"] != str(only_tcin)]
        elif keep_tcin is not None:
            tab.cart_items[:] = [i for i in tab.cart_items if i["tcin"] == str(keep_tcin)]
        else:
            tab.cart_items[:] = []
        return True, n0 - len(tab.cart_items)

    ex._delete_cart_items = _del

    async def _gp():
        return tab

    ex.session_manager.get_page = _gp
    return c, ex, tab


def _dispatch_other(c, ex, tab, env):
    """Dispatch 2: the throttle is over, another TCIN's 401'd add silently landed,
    the identity races SO. Returns (result, cart at the end, reached checkout?)."""
    tab.throttled = False
    tab.cart_items.append({"id": f"CI-{SO}", "tcin": SO, "qty": 1})
    ex._fast_selling_until = 0.0

    async def _fl2(t, tcin, qty, hdrs):
        return json.loads(json.dumps(ATC401_5))

    ex._api_fast_lane = _fl2
    c.t += 120
    n0 = len(ex.statuses)
    with S.in_tmp_cwd(), S.env(**env), S.fake_time(c):
        try:
            r2 = S.run(ex._execute_purchase_impl(SO, quantity=2))
        except S.LegacyReached:
            r2 = {"reason": "LEGACY_CHECKOUT_REACHED"}
    return r2, [i["tcin"] for i in tab.cart_items], "checking_out" in ex.statuses[n0:]


def test_v5_wc1_presumed_eviction_flags_strike_line():
    strike_fs = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
                 "pre": {"status": 201, "n": 1, "tcins": [ST], "qty": 2, "pi": []},
                 "po": {"status": 429, "body": S.FS_BODY, "fired": True}, "skip": ""}
    strike_pre429 = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
                     "pre": {"status": 429, "body": S.FS_BODY},
                     "po": {"status": 0, "body": "", "fired": False}, "skip": "pre_429"}
    cases = (("po424", strike_fs, [S.t_po(424, "{}", rej=(424, "RESERVATION_FAILURE"))]),
             ("pre400", strike_pre429, [S.t_pre(400, "")]))
    for label, fl1, script in cases:
        for fail in (False, True):
            c, ex, tab = _world_ex(fl1, list(script), [ST], fail_delete=fail)
            with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
                r1 = S.run(ex._execute_purchase_impl(ST, quantity=2))
            d = getattr(ex, "_woncart_dirty", None)
            tag = f"{label}_{'delete_fails' if fail else 'delete_ok'}"
            check(f"v5_A_{tag}_presumed_eviction_flags_line", r1.get("won_cart_exit") == "cart_evicted"
                  and isinstance(d, dict) and d.get("tcin") == ST, f"r1={r1} dirty={d}")
            r2, cart, co = _dispatch_other(c, ex, tab, ENV5)
            if fail:
                check(f"v5_A_{tag}_next_dispatch_fires_nothing",
                      r2.get("reason") == "held_cart_release_failed" and not co, f"r2={r2} co={co}")
            else:
                check(f"v5_A_{tag}_our_line_deleted_before_other_shot", ST not in cart,
                      f"r2={r2} cart={cart} co={co}")
    # Control: an ordinary won cart (201, not a strike) keeps HEAD's presume rule.
    won = {"atc": {"status": 201, "body": "", "cart_items": [{"tcin": ST, "quantity": 2}]},
           "pre": {"status": 201, "n": 1, "tcins": [ST], "qty": 2, "pi": []},
           "po": {"status": 429, "body": S.FS_BODY, "fired": True}, "skip": ""}
    c, ex, tab = _world_ex(won, [S.t_po(424, "{}", rej=(424, "RESERVATION_FAILURE"))], [ST])
    with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
        r1 = S.run(ex._execute_purchase_impl(ST, quantity=2))
    check("v5_A_ordinary_won_cart_presume_unchanged", r1.get("won_cart_exit") == "cart_evicted"
          and getattr(ex, "_woncart_dirty", None) is None, f"r1={r1} dirty={getattr(ex, '_woncart_dirty', None)}")


def test_v5_foreign_bail_flags_strike_line():
    foreign = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
               "pre": {"status": 201, "n": 2, "tcins": [ST, SO], "qty": 2, "pi": []},
               "po": {"status": 0, "body": "", "fired": False}, "skip": "foreign_cart_item"}
    env = dict(ENV5, TARGET_API_CART_CLEAR="true")
    for fail in (False, True):
        c, ex, tab = _world_ex(foreign, [], [ST, SO], fail_delete=fail)
        with S.in_tmp_cwd(), S.env(**env), S.fake_time(c):
            r1 = S.run(ex._execute_purchase_impl(ST, quantity=2))
        d = getattr(ex, "_woncart_dirty", None)
        tag = "delete_fails" if fail else "delete_ok"
        check(f"v5_B_{tag}_false_clear_still_flags_line", r1.get("reason") == "foreign_cart_cleared"
              and isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "strike_foreign"
              and ST in [i["tcin"] for i in tab.cart_items], f"r1={r1} dirty={d} log={tab.log}")
        tab.cart_items[:] = [i for i in tab.cart_items if i["tcin"] != SO]     # _dispatch_other re-adds SO
        r2, cart, co = _dispatch_other(c, ex, tab, env)
        if fail:
            check(f"v5_B_{tag}_next_dispatch_fires_nothing",
                  r2.get("reason") == "held_cart_release_failed" and not co, f"r2={r2} co={co}")
        else:
            check(f"v5_B_{tag}_our_line_deleted_before_other_shot", ST not in cart, f"r2={r2} cart={cart}")
    # Control: a non-strike foreign bail (flag on) sets no flag, as at HEAD.
    plain = dict(foreign, atc={"status": 201, "body": "", "cart_items": [{"tcin": ST, "quantity": 2}]})
    c, ex, tab = _world_ex(plain, [], [ST, SO])
    with S.in_tmp_cwd(), S.env(**env), S.fake_time(c):
        S.run(ex._execute_purchase_impl(ST, quantity=2))
    check("v5_B_non_strike_bail_unchanged", getattr(ex, "_woncart_dirty", None) is None)


def test_v5_held_presume_never_drops_strike_marker():
    for strike_marker in (True, False):
        c, ex, tab = _world_ex({}, [S.t_pre(429, S.FS_BODY)] * 40, [ST])
        if strike_marker:                  # dispatch 1: the strike loop leaves a held marker
            ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
            with S.in_tmp_cwd(), S.env(**dict(ENV5, TARGET_WONCART_CALL_MAX_S="30")), S.fake_time(c):
                r1 = S.run(ex._execute_purchase_impl(ST, quantity=2))
            check("v5_C_strike_loop_left_tagged_marker", r1.get("reason") == "won_cart_held"
                  and isinstance(ex._held_cart, dict) and ex._held_cart.get("strike") is True,
                  f"r1={r1} held={ex._held_cart}")
        else:                              # an ordinary (non-strike) held marker: HEAD's presume rule
            ex._held_cart = _held_full(c, created=c.t - 100, first_201_ts=c.t - 100, last_ticket_ts=c.t - 60)

        async def _fl2(t, tcin, qty, hdrs):
            return json.loads(json.dumps(ATC401_5))

        ex._api_fast_lane = _fl2
        c.t += 60
        ex._execute_started_at = c.t
        ex._mgr_submit_ts = c.t
        ex.ride_return = c.t + 290.0
        n0 = len(ex.statuses)
        with S.in_tmp_cwd(), S.env(**dict(ENV5, TARGET_WONCART_CALL_MAX_S="30")), S.fake_time(c):
            try:
                r2 = S.run(ex._execute_purchase_impl(ST, quantity=2))
            except S.LegacyReached:
                r2 = {"reason": "LEGACY_CHECKOUT_REACHED"}
        co = "checking_out" in ex.statuses[n0:]
        h, d = ex._held_cart, getattr(ex, "_woncart_dirty", None)
        tracked = ((isinstance(h, dict) and h.get("tcin") == ST) or (isinstance(d, dict) and d.get("tcin") == ST))
        if strike_marker:
            check("v5_C_strike_marker_not_presumed_gone", tracked and not co
                  and r2.get("reason") != "LEGACY_CHECKOUT_REACHED", f"r2={r2} held={h} dirty={d} co={co}")
        else:
            check("v5_C_ordinary_marker_presume_unchanged", h is None and r2.get("reason") != "won_cart_held",
                  f"r2={r2} held={h}")


def test_v5_cvv_ledger_after_unread_ticket():
    env = dict(ENV5, TARGET_FAST_LANE_CVV="1")
    c = S.Clock()
    ex = S.bare(c, cvv="123", cvv_required=True)
    ws = {"v": c.t - 10}
    ex._stock_live_fn = lambda t: {"live": True, "window_start": ws["v"]}
    ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
    step_timeout = {"raise": asyncio.TimeoutError(), "abort_read": {"s": "po", "aborted": False}, "out": {}}
    tab = S.TicketTab(ex, [step_timeout, S.t_pre(429, S.FS_BODY)], c)
    with S.in_tmp_cwd(), S.env(**env), S.fake_time(c):
        r1 = S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
    d = getattr(ex, "_woncart_dirty", None)
    first_cvv = bool(tab.tickets) and "const CVV_FIRST = true" in tab.tickets[0][2]
    check("v5_cvv_unread_ticket_ledger_unknown", first_cvv and r1.get("ambiguous_commit") is True
          and isinstance(d, dict) and d.get("cvv_put") == "unknown", f"r1={r1} dirty={d} first_cvv={first_cvv}")
    c.t += 1900
    ws["v"] = c.t - 5
    ex._execute_started_at = c.t
    ex._mgr_submit_ts = c.t
    ex.ride_return = c.t + 290.0
    n0 = len(tab.tickets)
    with S.in_tmp_cwd(), S.env(**env), S.fake_time(c, stop_after=1):
        try:
            S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
        except BaseException:  # noqa: BLE001 (fake_time stop)
            pass
    t2 = tab.tickets[n0][2] if len(tab.tickets) > n0 else ""
    check("v5_cvv_next_strike_sends_no_second_put", bool(t2) and "const CVV_FIRST = true" not in t2
          and "const CVV_REACTIVE = true" not in t2, f"tickets={len(tab.tickets) - n0}")
    # Unit: only a rendered PUT, a 'none' ledger and the flag move the ledger.
    ex = S.bare(S.Clock())
    for flag, rendered, start, want in (("1", True, "none", "unknown"), ("0", True, "none", "none"),
                                        ("1", False, "none", "none"), ("1", True, "ok", "ok")):
        L = {"cvv_put": start}
        with S.env(**dict(ENV5, TARGET_HELD_LINE_FLIP_STRIKE=flag)):
            ex._ticket_cvv_maybe_sent(L, rendered)
        check(f"v5_cvv_unit_flag{flag}_rendered{rendered}_{start}->{want}", L["cvv_put"] == want, str(L))


# ── 7. v6: the legacy-checkout cart guard (the sink) + verifier #5 paths A / B / C ──
# Whatever exit left it untracked, a line of another item must never ride into the
# legacy checkout, which buys the WHOLE cart.
def test_v6_legacy_checkout_cart_guard():
    # Route 1: the silent hold. An UNTRACKED line of ST sits in the cart; the identity
    # races SO, whose 401'd add silently landed.
    for flag in ("1", "0"):
        for fail in (False, True):
            c, ex, tab = _world_ex(ATC401_5, [], [ST, SO], fail_delete=fail)
            tab.throttled = False
            with S.in_tmp_cwd(), S.env(**dict(ENV5, TARGET_HELD_LINE_FLIP_STRIKE=flag)), S.fake_time(c):
                r = S.run(ex._execute_purchase_impl(SO, quantity=2))
            co, cart = "checking_out" in ex.statuses, [i["tcin"] for i in tab.cart_items]
            tag = f"hold_flag{flag}_{'delete_fails' if fail else 'delete_ok'}"
            if flag == "0":
                check(f"v6_guard_{tag}_head_unchanged", co and ST in cart, f"r={r} co={co} cart={cart}")
            elif fail:
                check(f"v6_guard_{tag}_no_checkout", not co and r.get("reason") == "held_cart_release_failed",
                      f"r={r} co={co} cart={cart}")
            else:
                check(f"v6_guard_{tag}_other_line_removed_first", co and cart == [SO],
                      f"r={r} co={co} cart={cart} deletes={ex.deletes}")
    # Route 2: the legacy add (fast lane cooling down) answers 201. Its parsed cart_items
    # list ONLY the line it added (root tcin + cart_item_id body, verifier #6), so it
    # is never evidence: the guard reads the cart (v7).
    added = {"status": 201, "body": "{}", "cart_items": [{"tcin": SO, "quantity": 2}]}
    for flag in ("1", "0"):
        c, ex, tab = _world_ex(ATC401_5, [], [ST, SO])
        tab.throttled = False
        tab.legacy = [dict(added)]
        ex._fast_selling_until = c.t + 300
        with S.in_tmp_cwd(), S.env(**dict(ENV5, TARGET_HELD_LINE_FLIP_STRIKE=flag)), S.fake_time(c):
            r = S.run(ex._execute_purchase_impl(SO, quantity=2))
        co, cart = "checking_out" in ex.statuses, [i["tcin"] for i in tab.cart_items]
        if flag == "1":
            check("v6_guard_legacy201_reads_cart_and_removes_other_line", co and cart == [SO]
                  and "cart_read" in tab.log, f"r={r} co={co} cart={cart} log={tab.log} deletes={ex.deletes}")
        else:
            check("v6_guard_legacy201_flag0_head_unchanged", co and ST in cart, f"r={r} co={co} cart={cart}")
    # Route 3: the 201 is in hand but the cart read is throttled -> no checkout.
    c, ex, tab = _world_ex(ATC401_5, [], [ST, SO])
    tab.throttled = True
    tab.legacy = [dict(added)]
    ex._fast_selling_until = c.t + 300
    with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
        r = S.run(ex._execute_purchase_impl(SO, quantity=2))
    check("v6_guard_unreadable_cart_no_checkout", "checking_out" not in ex.statuses
          and r.get("reason") == "held_cart_release_failed", f"r={r} statuses={ex.statuses}")


def test_v7_guard_evidence():
    # A complete silent-hold read is enough: 0 extra cart reads on a clean cart.
    c, ex, tab = _world_ex(ATC401_5, [], [SO])
    tab.throttled = False
    with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
        r = S.run(ex._execute_purchase_impl(SO, quantity=2))
    check("v7_guard_complete_hold_read_no_extra_read", "checking_out" in ex.statuses
          and "hold_read" in tab.log and "cart_read" not in tab.log, f"r={r} log={tab.log}")
    # A line with no tcin: the hold read's filter drops it, its count does not -> fresh read.
    c, ex, tab = _world_ex(ATC401_5, [], ["", SO])
    tab.throttled = False
    with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
        r = S.run(ex._execute_purchase_impl(SO, quantity=2))
    cart = [i["tcin"] for i in tab.cart_items]
    check("v7_guard_tcinless_line_forces_read_and_delete", "cart_read" in tab.log and cart == [SO]
          and "checking_out" in ex.statuses, f"r={r} log={tab.log} cart={cart}")
    # A 2xx read whose body carried no cart_items array is not evidence of an empty cart.
    c, ex, tab = _world_ex(ATC401_5, [], [ST, SO])
    tab.throttled = False
    tab.legacy = [{"status": 201, "body": "{}", "cart_items": [{"tcin": SO, "quantity": 2}]}]
    ex._fast_selling_until = c.t + 300
    _ev0 = tab.evaluate

    async def _no_array(js, await_promise=False, **kw):
        if "has_items" in js and "const MODE = '" not in js:
            tab.log.append("cart_read")
            return {"ok": True, "status": 200, "has_items": False, "items": []}
        return await _ev0(js, await_promise, **kw)

    tab.evaluate = _no_array
    with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
        r = S.run(ex._execute_purchase_impl(SO, quantity=2))
    check("v7_guard_read_without_array_no_checkout", "checking_out" not in ex.statuses
          and r.get("reason") == "held_cart_release_failed", f"r={r} log={tab.log}")


class _GuardTab(S.TicketTab):
    """The REAL _delete_cart_items against a scripted cart: cart reads come from
    `reads` (a list; the last entry repeats; 'live' = the current cart), DELETEs
    answer `del_status` and remove the line on 2xx."""
    def __init__(self, ex, c, cart, reads, del_status=204):
        super().__init__(ex, [], c, cart_items=[{"id": f"CI-{t or 'x'}", "tcin": t, "qty": 2} for t in cart])
        self.reads, self.del_status, self.n_reads = list(reads), del_status, 0

    async def evaluate(self, js, await_promise=False, **kw):
        if "method: 'DELETE'" in js:
            cid = __import__("re").search(r"cart_items/([A-Za-z0-9_-]+)'", js).group(1)
            self.deleted.append(cid)
            if 200 <= self.del_status < 300:
                self.cart_items = [i for i in self.cart_items if i["id"] != cid]
            return self.del_status
        if "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            self.n_reads += 1
            r = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
            if r == "live":
                return {"ok": True, "status": 200, "has_items": True, "items": [dict(i) for i in self.cart_items]}
            return dict(r)
        return await super().evaluate(js, await_promise, **kw)


def test_v8_guard_proves_removal():
    NOARR = {"ok": True, "status": 200, "has_items": False, "items": []}
    cases = (
        ("clean_after_delete", ["live"], 204, True),
        ("no_array_reread", ["live", NOARR], 204, False),
        ("delete_500", ["live"], 500, False),
        ("stale_reread_shows_other", ["live", {"ok": True, "status": 200, "has_items": True,
                                               "items": [{"id": "CI-" + ST, "tcin": ST, "qty": 2},
                                                         {"id": "CI-" + SO, "tcin": SO, "qty": 2}]}], 204, False),
        ("tcinless_line", ["live"], 204, True),
    )
    for label, reads, del_status, want_proceed in cases:
        c = S.Clock()
        ex = S.bare(c)
        ex._delete_cart_items = PurchaseExecutor._delete_cart_items.__get__(ex)   # the real one
        cart = ["", SO] if label == "tcinless_line" else [ST, SO]
        tab = _GuardTab(ex, c, cart, reads, del_status)
        with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
            r = S.run(ex._legacy_checkout_cart_guard(tab, SO, c.t))
        proceeded = r is None
        left = [i["tcin"] for i in tab.cart_items]
        check(f"v8_guard_{label}_proceed={want_proceed}", proceeded == want_proceed
              and (not proceeded or left == [SO]), f"r={r} left={left} deleted={tab.deleted} reads={tab.n_reads}")


class _ReadModeTab(S.TicketTab):
    """Tickets as scripted; every cart read answers per `mode`: 'noarr' = a 2xx
    with no cart_items array (verifier #8), 'gone' = a 2xx array without our line,
    'there' = a 2xx array with our line."""
    def __init__(self, ex, script, c, mode):
        super().__init__(ex, script, c, cart_items=[{"id": "CI-1234567890", "tcin": ST, "qty": 2}])
        self.mode = mode

    async def evaluate(self, js, await_promise=False, **kw):
        if "const MODE = '" not in js and "e.abort = true" not in js and "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            if self.mode == "noarr":
                return {"ok": True, "status": 200, "has_items": False, "items": []}
            items = [] if self.mode == "gone" else [dict(i) for i in self.cart_items]
            return {"ok": True, "status": 200, "has_items": True, "items": items}
        return await super().evaluate(js, await_promise, **kw)


def test_v9_no_array_reads_keep_strike_line_flagged():
    # P3: a strike-loop ticket's pre_checkout 2xx parses as an empty cart.
    # P2: a strike-loop ticket sees qty over, and the exit's delete removes 0 lines.
    # cap: the per-cart ticket cap retires the line, and the delete removes 0 lines.
    cases = (("P3_cart_empty", [S.t_skip("cart_empty", n=0, tcins=[], qty=0)], {}),
             ("P2_qty_over", [S.t_skip("cart_qty_over", qty=4)], {}),
             ("cap", [S.t_pre(429, S.FS_BODY)] * 3, {"TARGET_WONCART_MAX_TICKETS": "1"}))
    for label, script, extra in cases:
        for mode, want in (("noarr", True), ("there", True), ("gone", False)):
            c = S.Clock()
            ex = S.bare(c)
            ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
            ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
            ex.delete_result = (True, 0)               # the delete's own read found nothing
            tab = _ReadModeTab(ex, list(script), c, mode)
            with S.in_tmp_cwd(), S.env(**dict(_loop_env(), TARGET_HELD_CART_TTL_S="900", **extra)), \
                    S.fake_time(c, stop_after=6):
                try:
                    r = S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
                except BaseException as e:  # noqa: BLE001
                    r = {"raised": type(e).__name__}
            d, h = getattr(ex, "_woncart_dirty", None), ex._held_cart
            tracked = ((isinstance(d, dict) and d.get("tcin") == ST)
                       or (isinstance(h, dict) and h.get("tcin") == ST))
            check(f"v9_{label}_{mode}_tracked={want}", tracked == want,
                  f"r={r} dirty={d} held={h} tickets={len(tab.tickets)}")
    # P1: the in-chain strike's qty-guard delete removes 0 lines.
    qover = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
             "pre": {"status": 200, "n": 1, "tcins": [ST], "qty": 4, "pi": []},
             "po": {"status": 0, "body": "", "fired": False}, "skip": "cart_qty_over"}
    for mode, want in (("noarr", True), ("gone", False)):
        c, ex, tab = _world_ex(qover, [], [ST])
        tab.throttled = False

        async def _zero(*a, **k):
            return True, 0

        ex._delete_cart_items = _zero
        _ev0 = tab.evaluate

        async def _ev(js, await_promise=False, _m=mode, _e=_ev0, **kw):
            if "has_items" in js and "const MODE = '" not in js:
                if _m == "noarr":
                    return {"ok": True, "status": 200, "has_items": False, "items": []}
                return {"ok": True, "status": 200, "has_items": True, "items": []}
            return await _e(js, await_promise, **kw)

        tab.evaluate = _ev
        with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
            r = S.run(ex._execute_purchase_impl(ST, quantity=2))
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST
        check(f"v9_P1_qty_guard_{mode}_flagged={want}", got == want, f"r={r} dirty={d}")


def test_v11_atc_stage_timeout_flags_strike_line():
    class _AtcAbortTab:
        async def evaluate(self, js, await_promise=False, **kw):
            if "e.abort = true" in js:
                return {"s": "atc", "aborted": True, "atc": 0}
            raise asyncio.TimeoutError()

    for flag, want in (("1", True), ("0", False)):
        c = S.Clock(time.time())
        ex = S.bare(c)
        with S.in_tmp_cwd(), S.env(**dict(S_ENV, TARGET_FASTLANE_STAGE_TRACK="1",
                                          TARGET_HELD_LINE_FLIP_STRIKE=flag)):
            fl = S.run(ex._api_fast_lane(_AtcAbortTab(), ST, 2, json.dumps({"x": "y"})))
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "strike_atc_timeout"
        check(f"v11_atc_timeout_flag{flag}_flagged={want}", fl.get("skip") == "evaluate_timeout_atc"
              and got == want and (want or d is None), f"fl={fl} dirty={d}")


def test_v10_strike_requires_reentry():
    ex = S.bare(S.Clock())
    for reentry, want in (("1", True), ("0", False)):
        with S.env(**dict(S_ENV, TARGET_HELD_CART_REENTRY=reentry)):
            got = ex._held_line_strike_active(ST)
        check(f"v10_strike_active_reentry{reentry}={want}", got is want, str(got))


def test_v7_cancel_windows_keep_line_tracked():
    class _AbortCancelTab:
        async def evaluate(self, js, await_promise=False, **kw):
            if "e.abort = true" in js:
                raise asyncio.CancelledError()
            raise asyncio.TimeoutError()

    for flag, want in (("1", True), ("0", False)):    # the fast lane's read-and-abort
        c = S.Clock(time.time())
        ex = S.bare(c)
        raised = False
        with S.in_tmp_cwd(), S.env(**dict(S_ENV, TARGET_FASTLANE_STAGE_TRACK="1",
                                          TARGET_HELD_LINE_FLIP_STRIKE=flag)):
            try:
                S.run(ex._api_fast_lane(_AbortCancelTab(), ST, 2, json.dumps({"x": "y"})))
            except BaseException:  # noqa: BLE001
                raised = True
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "strike_cancelled"
        check(f"v7_cancel_in_fl_abort_read_flag{flag}_flagged={want}", raised and got == want
              and (want or d is None), f"raised={raised} dirty={d}")

    async def _cancel(*a, **k):
        raise asyncio.CancelledError()

    foreign = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
               "pre": {"status": 201, "n": 2, "tcins": [ST, SO], "qty": 2, "pi": []},
               "po": {"status": 0, "body": "", "fired": False}, "skip": "foreign_cart_item"}
    qover = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": [], "strike": True},
             "pre": {"status": 200, "n": 1, "tcins": [ST], "qty": 4, "pi": []},
             "po": {"status": 0, "body": "", "fired": False}, "skip": "cart_qty_over"}
    for label, fl1, attr, why in (("foreign_clear", foreign, "_clear_cart", "strike_foreign"),
                                  ("qty_delete", qover, "_delete_cart_items", "strike_qty_stuck")):
        c, ex, tab = _world_ex(fl1, [], [ST, SO] if label == "foreign_clear" else [ST])
        setattr(ex, attr, _cancel)
        raised = False
        with S.in_tmp_cwd(), S.env(**ENV5), S.fake_time(c):
            try:
                S.run(ex._execute_purchase_impl(ST, quantity=2))
            except BaseException:  # noqa: BLE001
                raised = True
        d = getattr(ex, "_woncart_dirty", None)
        check(f"v7_cancel_in_{label}_flags_line", raised and isinstance(d, dict) and d.get("tcin") == ST
              and d.get("why") == why, f"raised={raised} dirty={d}")

    class _TicketAbortCancel(S.TicketTab):
        async def evaluate(self, js, await_promise=False, **kw):
            if "e.abort = true" in js:
                raise asyncio.CancelledError()
            return await super().evaluate(js, await_promise, **kw)

    c = S.Clock()                                      # the ticket's read-and-abort (claim 5)
    ex = S.bare(c, cvv="123", cvv_required=True)
    ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
    ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
    tab = _TicketAbortCancel(ex, [{"raise": asyncio.TimeoutError(), "out": {}}], c)
    with S.in_tmp_cwd(), S.env(**dict(_loop_env(), TARGET_FAST_LANE_CVV="1")), S.fake_time(c):
        try:
            S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
        except BaseException:  # noqa: BLE001
            pass
    d = getattr(ex, "_woncart_dirty", None)
    first_cvv = bool(tab.tickets) and "const CVV_FIRST = true" in tab.tickets[0][2]
    check("v7_cancel_in_ticket_abort_read_cvv_unknown", first_cvv and isinstance(d, dict)
          and d.get("cvv_put") == "unknown", f"dirty={d} first_cvv={first_cvv}")


def test_v6_cancel_and_recovery_keep_line_tracked():
    class _CancelTab:
        async def evaluate(self, js, await_promise=False, **kw):
            raise asyncio.CancelledError()

    for flag, want in (("1", True), ("0", False)):    # path B: a cancelled in-chain strike
        c = S.Clock(time.time())
        ex = S.bare(c)
        raised = False
        with S.in_tmp_cwd(), S.env(**dict(S_ENV, TARGET_HELD_LINE_FLIP_STRIKE=flag)):
            try:
                S.run(ex._api_fast_lane(_CancelTab(), ST, 2, json.dumps({"x": "y"})))
            except BaseException:  # noqa: BLE001 (the cancel must propagate)
                raised = True
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "strike_cancelled"
        check(f"v6_B_cancel_flag{flag}_flagged={want}", raised and got == want and (want or d is None),
              f"raised={raised} dirty={d}")

    for flag, want in (("1", True), ("0", False)):    # path A: a stacked held line, delete fails
        c = S.Clock()
        ex = S.bare(c)
        ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
        ex._held_cart = _held_full(c, created=c.t - 100, first_201_ts=c.t - 100, last_ticket_ts=c.t - 60)
        ex.delete_result = (False, 0)
        tab = _World(ex, [], c, [ST], throttled=False)
        tab.cart_items[0]["qty"] = 4
        with S.in_tmp_cwd(), S.env(**dict(_loop_env(), TARGET_HELD_CART_TTL_S="900",
                                          TARGET_HELD_LINE_FLIP_STRIKE=flag)), S.fake_time(c):
            r = S.run(ex._held_cart_entry(tab, ST, 2, c.t))
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "held_qty_stuck"
        check(f"v6_A_stacked_held_flag{flag}_flagged={want}",
              (r or {}).get("reason") == "held_cart_release_failed" and got == want and (want or d is None),
              f"r={r} dirty={d}")

    class _RecTab:
        async def get(self, url):
            return None

    for flag, want in (("1", True), ("0", False)):    # path C: error recovery drops the marker
        c, ex, tab = _world_ex({}, [], [ST])
        ex._held_cart = _held_full(c)
        ex.session_manager.browser = type("B", (), {"tabs": [_RecTab()]})()

        async def _boom(*a, **k):
            raise RuntimeError("boom")

        async def _clear_true(t):
            return True

        ex._held_cart_entry = _boom
        ex._clear_cart = _clear_true
        with S.in_tmp_cwd(), S.env(**dict(ENV5, TARGET_HELD_LINE_FLIP_STRIKE=flag)), S.fake_time(c):
            r = S.run(ex._execute_purchase_impl(ST, quantity=2))
        d = getattr(ex, "_woncart_dirty", None)
        got = isinstance(d, dict) and d.get("tcin") == ST and d.get("why") == "recovery_clear_unproven"
        check(f"v6_C_recovery_flag{flag}_flagged={want}", ex._held_cart is None and got == want
              and (want or d is None), f"r={r} dirty={d} held={ex._held_cart}")


def test_v6_cvv_ledger_on_cancel():
    env = dict(_loop_env(), TARGET_FAST_LANE_CVV="1")
    # V1: a dirty-flag strike whose CVV_FIRST ticket is cancelled.
    c = S.Clock()
    ex = S.bare(c, cvv="123", cvv_required=True)
    ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
    ex._woncart_dirty = {"tcin": ST, "ts": c.t - 300, "why": "po_401_streak", "cvv_put": "none"}
    tab = S.TicketTab(ex, [{"raise": asyncio.CancelledError(), "out": {}}], c)
    with S.in_tmp_cwd(), S.env(**env), S.fake_time(c):
        try:
            S.run(ex._woncart_dirty_release(tab, ST, c.t, 2))
        except BaseException:  # noqa: BLE001
            pass
    d = getattr(ex, "_woncart_dirty", None)
    first_cvv = bool(tab.tickets) and "const CVV_FIRST = true" in tab.tickets[0][2]
    check("v6_cvv_cancel_dirty_strike_unknown", first_cvv and isinstance(d, dict)
          and d.get("cvv_put") == "unknown", f"dirty={d} first_cvv={first_cvv}")
    # V2: a TTL-expired held marker struck in a new window, ticket cancelled.
    c = S.Clock()
    ex = S.bare(c, cvv="123", cvv_required=True)
    ex._stock_live_fn = lambda t, c=c: {"live": True, "window_start": c.t - 10}
    h = _held_full(c)                                  # created 1000 s ago > TTL 60
    ex._held_cart = h
    tab = S.TicketTab(ex, [{"raise": asyncio.CancelledError(), "out": {}}], c)
    with S.in_tmp_cwd(), S.env(**env), S.fake_time(c):
        try:
            S.run(ex._held_cart_entry(tab, ST, 2, c.t))
        except BaseException:  # noqa: BLE001
            pass
    first_cvv = bool(tab.tickets) and "const CVV_FIRST = true" in tab.tickets[0][2]
    check("v6_cvv_cancel_held_marker_unknown", first_cvv and h.get("cvv_put") == "unknown",
          f"marker cvv={h.get('cvv_put')} first_cvv={first_cvv} tickets={len(tab.tickets)}")


def main():
    for fn in (test_flag_parsing, test_window_fresh, test_legacy_reason, test_eligible,
               test_kept_line_predicate, test_js_strict_qty,
               test_real_kept_line_never_bought_by_legacy, test_real_loop_terminal_keeps_line_tracked,
               test_real_cvv_state_carried, test_real_strike_timeout_flags_line,
               test_js_off_is_byte_identical, test_js_scenarios,
               test_placed_records_order_and_disables_strike,
               test_placed_strike_returns_before_legacy_branches,
               test_dirty_release_strikes_through_the_loop,
               test_held_entry_strikes_through_the_loop,
               test_ledger_marks_strike_source,
               test_v4_terminal_strike_keeps_line_tracked,
               test_v4_presumed_eviction_keeps_strike_line_flagged,
               test_v4_failed_release_keeps_line_tracked,
               test_v5_wc1_presumed_eviction_flags_strike_line,
               test_v5_foreign_bail_flags_strike_line,
               test_v5_held_presume_never_drops_strike_marker,
               test_v5_cvv_ledger_after_unread_ticket,
               test_v6_legacy_checkout_cart_guard,
               test_v6_cancel_and_recovery_keep_line_tracked,
               test_v6_cvv_ledger_on_cancel,
               test_v7_guard_evidence,
               test_v7_cancel_windows_keep_line_tracked,
               test_v8_guard_proves_removal,
               test_v9_no_array_reads_keep_strike_line_flagged,
               test_v10_strike_requires_reentry,
               test_v11_atc_stage_timeout_flags_strike_line):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAIL:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

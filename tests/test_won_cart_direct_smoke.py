#!/usr/bin/env python3
"""WC-1 (hot-sku 0916 plan P1): won-cart direct checkout loop — offline smoke.

WHY: 09-11 and 09-16 each produced exactly ONE cart. Each got one provably
in-stock checkout ticket after a ~26.5 s nav/DOM detour, then 45 s holds. The
only through-FAST_SELLING conversion ever (08-04) was a place-order-only POST
~45 s after the FS. TARGET_WONCART_DIRECT=1 fires the first ticket seconds after
the 201 (probe schedule 5,15 once per cart), then the proven 45 s place-order-only
shape, capped by the executor's and the manager's real deadlines. Every flag
defaults off; the fast-lane JS stays byte-identical (see test_fast_lane_golden).

Covers (plan P1 tests a-e):
  (a) the ticket JS under Node: strict gate, modes x CVV constants, atomic abort,
      non-enumerable stage key, <=1 CVV PUT per cart, never the ATC URL;
  (b) QG in the fast-lane JS (pi intact with a null item, qty 4 -> cart_qty_over)
      and its caller (bounded delete of our line, never the legacy path);
  (c) the loop on a bare executor: scripted tab/ticket, recorded sleeps on a fake
      clock, scripted probe, stubbed ride deadline; exits through a copy of the
      manager's transient/terminal classifier parsed from the REAL source; quiet
      mode; eligibility; the call site inside the real _execute_purchase_impl;
  (d) the manager's stock_snapshot / any_stock_live;
  (e) SCR note_stock_read + the real sweep-ingest wiring;
  (f) stage S2c, plan P3 (WC-3): the held-cart check inside the real
      _execute_purchase_impl (exact/idle/empty/429/foreign/over-qty/other-TCIN/
      TTL/cap/CVV/error, plus an end-to-end held po_only order), the boot cart
      audit, the selective suspect-clear, the error-recovery marker drop, and
      every new reason through the manager classifier (digit-free errors: the
      manager restarts the browser on '1011', a hot-TCIN prefix);
  (g) stage S2c, plan P4 (WC-2) impl pieces: ride trim at purchase exit and the
      duplicate pre_checkout skip;
  (r1) review round R1 fixes: held re-entry never yields before a ticket, the
      ride survives a placed order's success tail, <= 1 CVV PUT per cart incl.
      the fast lane's own PUT, po_only falls back to pre_po on a possible late
      add, the page global leaves with its last entry, the boot audit deletes
      only what it read, expired held markers retire in the background, and a
      missing node FAILS the file.

Offline: no browser, no network (node subprocess only). Anything that could
append to logs/error_log.txt runs in a temp cwd.

Run: python tests/test_won_cart_direct_smoke.py
"""
from __future__ import annotations

import asyncio
import ast
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
import time as real_time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_ALL_FLAGS = (
    "TARGET_WONCART_DIRECT", "TARGET_AMBIGUOUS_COMMIT_LATCH", "TARGET_AMBIGUOUS_COMMIT_LATCH_S",
    "TARGET_FASTLANE_QTY_GUARD", "TARGET_HELD_CART_REENTRY", "TARGET_WONCART_SCHEDULE_S",
    "TARGET_WONCART_STEADY_GAP_S", "TARGET_WONCART_JITTER_S", "TARGET_WONCART_OOS_TAIL_TICKETS",
    "TARGET_WONCART_MAX_TICKETS", "TARGET_WONCART_CALL_MAX_S", "TARGET_WONCART_HEADROOM_S",
    "TARGET_WONCART_PRE_STREAK_MAX", "TARGET_WONCART_YIELD_FLEET", "TARGET_WON_CART_RIDE",
    "TARGET_WON_CART_RIDE_MAX_S", "TARGET_FAST_SELLING_COOLDOWN_S", "TARGET_STOCK_PROBE",
    "TARGET_STOCK_HYST_S", "TARGET_STOCK_PROBE_FRESH_S", "TARGET_FS_TICKET_LOG",
    "TARGET_FAST_LANE", "TARGET_API_PLACE_ORDER", "TARGET_FAST_LANE_CVV", "TARGET_ATC_BYTEMATCH",
    "TARGET_FOREIGN_CART_BAIL", "TARGET_RETRY_CHECKOUT_BUSY", "TARGET_QTY_CEILING",
    "TARGET_WARMUP_PAUSE_DURING_PURCHASE", "TARGET_DEBUG_AUTH", "TARGET_PDP_QTY_LOOKUP",
    "TARGET_FORCE_QTY_1", "TARGET_SHOT_TTL_REFRESH",
    # stage S2c (WC-3 / WC-2)
    "TARGET_BOOT_CART_AUDIT", "TARGET_HELD_CART_TTL_S", "TARGET_RESHOOT_FORCE_REWARM",
    "TARGET_HOLD_QUIET_WARMUP", "TARGET_WARMUP_CYCLE_SKIP_ON_STOCK", "TARGET_FASTLANE_SKIP_DUP_PRE",
    "TARGET_WON_CART_RIDE_CLEAN_EXIT",
    # stage S5 (FL-1)
    "TARGET_FASTLANE_STAGE_TRACK",
)
for _k in _ALL_FLAGS:
    os.environ.pop(_k, None)

import src.session.purchase_executor as pe_mod  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402
import src.monitoring.stock_check_resilient as scr_mod  # noqa: E402

_QUIET_LOG = logging.getLogger("wc1_test")
_QUIET_LOG.addHandler(logging.NullHandler())
_QUIET_LOG.propagate = False

TCIN = "1010892069"
OTHER = "1011407490"
FS_BODY = json.dumps({"message": "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"})
ORDER_BODY = json.dumps({"orders": [{"order_id": "OID-777", "reference_id": "R1"}]})
NODE = shutil.which("node")
PASSED: list = []
FAILED: list = []
_TMP = tempfile.mkdtemp(prefix="wc1_")
REAL_ASYNCIO = asyncio


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


ARMED = dict(TARGET_WONCART_DIRECT="1", TARGET_AMBIGUOUS_COMMIT_LATCH="1")


@contextlib.contextmanager
def in_tmp_cwd():
    old = os.getcwd()
    d = tempfile.mkdtemp(dir=_TMP)
    os.chdir(d)
    try:
        yield d
    finally:
        os.chdir(old)


def run(coro):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = asyncio.run(coro)
    run.last_out = buf.getvalue()
    return r


run.last_out = ""


# ───────────────────────── fake clock / asyncio shims ─────────────────────────

class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = float(t)


class TimeShim:
    def __init__(self, clock):
        self._c = clock

    def time(self):
        return self._c.t

    def __getattr__(self, name):
        return getattr(real_time, name)


class AsyncioShim:
    """pe_mod's `asyncio` during loop tests: sleep() advances the fake clock
    and is recorded; everything else is the real module."""

    def __init__(self, clock, stop_after=None):
        self._c = clock
        self.sleeps = []
        self._stop_after = stop_after

    async def sleep(self, s, *a, **k):
        self.sleeps.append(float(s))
        if self._stop_after is not None and len(self.sleeps) > self._stop_after:
            raise REAL_ASYNCIO.CancelledError()
        self._c.t += float(s)
        await REAL_ASYNCIO.sleep(0)

    def __getattr__(self, name):
        return getattr(REAL_ASYNCIO, name)


@contextlib.contextmanager
def fake_time(clock, stop_after=None):
    shim = AsyncioShim(clock, stop_after)
    saved = (pe_mod.time, pe_mod.asyncio)
    pe_mod.time = TimeShim(clock)
    pe_mod.asyncio = shim
    try:
        yield shim
    finally:
        pe_mod.time, pe_mod.asyncio = saved


# ───────────────────────────── executor stubs ────────────────────────────────

class _SM:
    account_id = "primary"
    browser = None

    def __init__(self, cvv=""):
        self._cvv = cvv
        self.saved = 0
        self.save_raises = None
        self.live = True

    def _load_account_cvv(self):
        return self._cvv

    async def save_session_state(self):
        self.saved += 1
        if self.save_raises:
            raise self.save_raises

    def is_purchase_in_progress(self):
        return self.live


def bare(clock, cvv="", cvv_required=False):
    ex = object.__new__(PurchaseExecutor)
    ex.test_mode = False
    ex.session_manager = _SM(cvv)
    ex.status_callback = None
    ex.logger = _QUIET_LOG
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._fastlane_placed = False
    ex._cvv_required = cvv_required
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    ex._fast_selling_until = 0.0
    ex._persist_cvv_challenge_flag = lambda: None
    ex._won_cart_ride_until = 0.0
    ex._execute_started_at = clock.t
    ex._mgr_submit_ts = clock.t
    ex._po_inflight = False
    ex._po_ambiguous = False
    ex._woncart_po_unresolved = False
    ex._held_cart = None
    ex._woncart_active_until = 0.0
    ex._cached_cart_headers = {"X-GyJwza5Z-a": "tok", "cookie": "c=1", "Referer": "r"}
    ex._cached_cart_headers_ts = clock.t
    ex._fl_stage_key = ""
    ex._woncart_ticket_seq = 0
    ex._woncart_refusal_logged = False
    ex._last_checkout_resp = {}
    ex._other_live_fn = None
    ex.probe = {"live": True}
    ex._stock_live_fn = lambda t: dict(ex.probe, window_start=clock.t - 3)
    ex.fs_notes = []
    ex._note_fast_selling_throttle = lambda: ex.fs_notes.append(clock.t)
    ex.deletes = []
    ex.delete_result = (True, 1)

    async def _del(tab, only_tcin=None, keep_tcin=None, budget_s=15.0, ids=None, abort_fn=None):
        d = {"only": only_tcin, "keep": keep_tcin, "budget": budget_s}
        if ids is not None:
            d["ids"] = list(ids)
        if abort_fn is not None:
            d["abort"] = abort_fn
        ex.deletes.append(d)
        return ex.delete_result

    ex._delete_cart_items = _del
    ex.ride_calls = []

    def _ride(fs_deadline):
        ex.ride_calls.append(fs_deadline)
        ex._won_cart_ride_until = clock.t + 1000.0
        return ex.ride_return

    ex.ride_return = ex._execute_started_at + 290.0
    ex._begin_won_cart_ride = _ride
    return ex


def t_pre(status, body="", skip=None):
    return {"out": {"mode": "pre_po", "pre": {"status": status, "body": body},
                    "po": {"status": 0, "body": "", "fired": False},
                    "cvv": {"put": -1}, "skip": skip or f"pre_{status}"}}


def t_skip(skip, parsed=True, status=200, **pre_extra):
    pre = {"status": status, "parsed": parsed, "n": 1, "tcins": [TCIN], "qty": 2}
    pre.update(pre_extra)
    return {"out": {"mode": "pre_po", "pre": pre, "po": {"status": 0, "body": "", "fired": False},
                    "cvv": {"put": -1}, "skip": skip}}


def t_prepo(po_status, po_body="", rej=None, put=-1):
    return {"out": {"mode": "pre_po",
                    "pre": {"status": 200, "parsed": True, "n": 1, "tcins": [TCIN], "qty": 2,
                            "cart_id": "CART-1234567890", "pi": [{"id": "PI-1234567890"}]},
                    "po": {"status": po_status, "body": po_body, "fired": True},
                    "cvv": {"put": put}, "skip": ""},
            "rej": rej}


def t_po(po_status, po_body="", rej=None):
    return {"out": {"mode": "po_only", "pre": {"status": 0},
                    "po": {"status": po_status, "body": po_body, "fired": True},
                    "cvv": {"put": -1}, "skip": ""},
            "rej": rej}


class TicketTab:
    """Scripted tab: ticket JS -> next scripted step (optionally setting the
    interceptor's reject fields); read-and-abort JS -> step['abort_read'];
    cart read / DELETE -> configured cart."""

    def __init__(self, ex, script, clock=None, cart_items=None):
        self.ex = ex
        self.script = list(script)
        self.clock = clock
        self.tickets = []          # (clock time, mode, js)
        self.aborts = 0
        self.cart_items = list(cart_items or [])
        self.deleted = []
        self.url = "https://www.target.com/"
        self._pending_abort = None

    async def evaluate(self, js, await_promise=False, **kw):
        if "e.abort = true" in js:
            self.aborts += 1
            return self._pending_abort
        if "const MODE = '" in js:
            mode = re.search(r"const MODE = '(\w+)'", js).group(1)
            self.tickets.append((self.clock.t if self.clock else 0.0, mode, js))
            step = self.script.pop(0) if self.script else t_pre(429)
            rej = step.get("rej")
            if rej:
                self.ex._checkout_rejected = True
                self.ex._checkout_reject_status, self.ex._checkout_reject_reason = rej
            if step.get("raise"):
                self._pending_abort = step.get("abort_read")
                raise step["raise"]
            return json.loads(json.dumps(step["out"]))
        if "method: 'DELETE'" in js:
            cid = re.search(r"cart_items/([A-Za-z0-9_-]+)'", js).group(1)
            self.deleted.append(cid)
            self.cart_items = [i for i in self.cart_items if i["id"] != cid]
            return 204
        if "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            return {"ok": True, "status": 200, "items": [dict(i) for i in self.cart_items]}
        raise AssertionError("unexpected evaluate: " + js[:80])


# ───────────────────────── (a) ticket JS under Node ──────────────────────────

NODE_HARNESS = r"""
const S = %s;
const calls = [];
const pend = [];
globalThis.fetch = (url, opts) => {
  const u = String(url);
  calls.push({url: u.split('?')[0], method: (opts && opts.method) || 'GET',
              body: (opts && opts.body) || null});
  let rule = null;
  for (const r of S.rules) { if (u.includes(r.match)) { rule = r; break; } }
  if (!rule) return Promise.reject(new Error('no stub rule for ' + u));
  rule._n = (rule._n || 0) + 1;
  const eff = rule.seq ? rule.seq[Math.min(rule._n - 1, rule.seq.length - 1)] : rule;
  const resp = {status: eff.status, text: async () => (eff.body === undefined ? '' : eff.body)};
  if (eff.hang) return new Promise((res) => pend.push(() => res(resp)));
  if (eff.throw) return Promise.reject(new Error(eff.throw));
  return Promise.resolve(resp);
};
(async () => {
  const p = (%s);
  let abortRead = null, enumDuring = null, hadKeyDuring = null;
  if (S.abort) {
    for (let i = 0; i < 50 && pend.length === 0; i++) await new Promise(r => setTimeout(r, 2));
    enumDuring = Object.keys(globalThis).includes(S.key);
    hadKeyDuring = !!(globalThis[S.key] && globalThis[S.key][S.n]);
    abortRead = (%s);
    while (pend.length) pend.shift()();
  }
  const out = await p;
  for (let i = 0; i < 5 && pend.length; i++) { pend.shift()(); await new Promise(r => setTimeout(r, 1)); }
  const lateAbort = (%s);
  console.log(JSON.stringify({
    out, calls, abortRead, enumDuring, hadKeyDuring, lateAbort,
    enumAfter: Object.keys(globalThis).includes(S.key),
    hasKeyAfter: Object.prototype.hasOwnProperty.call(globalThis, S.key),
    entryAfter: !!(globalThis[S.key] && globalThis[S.key][S.n] !== undefined),
  }));
})().catch(e => { console.log(JSON.stringify({error: String(e && e.stack || e)})); });
"""

KEY = "__0123456789ab"
PRE_M, PO_M, PUT_M, ATC_M = "pre_checkout", "v1/checkout?", "payment_instructions/", "cart_items"


def pre_body(items, pis=True):
    d = {"cart_id": "CART-1234567890", "cart_items": items}
    if pis:
        d["payment_instructions"] = [{"payment_instruction_id": "PI-1234567890",
                                      "payment_type": "CARD", "cvv_required": True}]
    return json.dumps(d)


def node_run(js, rules, n="t1", abort=False):
    abort_js = pe_mod.render_checkout_ticket_abort_js(KEY, n)
    scen = {"rules": rules, "abort": abort, "key": KEY, "n": n}
    src = NODE_HARNESS % (json.dumps(scen), js, abort_js, abort_js)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8", dir=_TMP) as f:
        f.write(src)
        path = f.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"node failed rc={proc.returncode}: {proc.stderr[:400]}")
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    if "error" in res:
        raise AssertionError("node harness error: " + res["error"][:400])
    return res


def ticket_js(mode="pre_po", q=2, cvv="", first=False, reactive=False, n="t1", pi="", cart=""):
    return pe_mod.render_checkout_ticket_js(KEY, n, TCIN, q, mode, json.dumps({"X-GyJwza5Z-a": "tok"}),
                                            cvv=cvv, cvv_first=first, cvv_reactive=reactive,
                                            pi_id=pi, cart_id=cart)


def kinds(res):
    out = []
    for c in res["calls"]:
        u = c["url"]
        if "pre_checkout" in u:
            out.append("pre")
        elif "payment_instructions" in u:
            out.append("put")
        elif u.endswith("/v1/checkout"):
            out.append("po")
        elif "cart_items" in u:
            out.append("ATC")
        else:
            out.append(u)
    return out


EXACT = [{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 2}]


def test_a_ticket_js_node():
    if not NODE:
        # R1 review (R1-TEST-3): the armed ticket JS must never go untested
        # silently — a missing node is a FAILURE, like test_fast_lane_checkout.
        check("a_node_available", False, "node not on PATH — ticket JS tests cannot run")
        return
    # Every mode x CVV_FIRST x CVV_REACTIVE on an exact cart: exactly one
    # checkout POST, PUT only when CVV_FIRST, never the ATC URL.
    for mode in ("pre_po", "po_only"):
        for first in (False, True):
            for reactive in (False, True):
                js = ticket_js(mode, cvv="123", first=first, reactive=reactive,
                               pi="PI-1234567890", cart="CART-1234567890")
                check(f"a_js_never_references_atc_url[{mode},{first},{reactive}]",
                      "web_checkouts/v1/cart_items" not in js)
                check(f"a_bool_literals[{mode},{first},{reactive}]",
                      f"const CVV_FIRST = {'true' if first else 'false'};" in js
                      and f"const CVV_REACTIVE = {'true' if reactive else 'false'};" in js)
                r = node_run(js, [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                                  {"match": PUT_M, "status": 200, "body": "{}"},
                                  {"match": PO_M, "status": 200, "body": ORDER_BODY}])
                k = kinds(r)
                want = (["pre"] if mode == "pre_po" else []) + (["put"] if first else []) + ["po"]
                check(f"a_exact_cart_one_checkout[{mode},first={first},reactive={reactive}]",
                      k == want and r["out"]["po"]["status"] == 200 and r["out"]["po"]["fired"] is True,
                      f"{k} {r['out']['po']}")
                check(f"a_no_atc_fetch[{mode},{first},{reactive}]", "ATC" not in k, str(k))
    # Non-JSON 2xx pre -> pre_unparsed, no place-order.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200, "body": "<html>busy</html>"},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_non_json_2xx_pre_unparsed", r["out"]["skip"] == "pre_unparsed" and kinds(r) == ["pre"]
          and r["out"]["pre"]["parsed"] is False, f"{r['out']['skip']} {kinds(r)}")
    # Missing quantity -> cart_qty_unknown.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200,
                                "body": pre_body([{"cart_item_id": "CI-1", "tcin": TCIN}])},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_missing_qty_unknown", r["out"]["skip"] == "cart_qty_unknown" and kinds(r) == ["pre"],
          f"{r['out']['skip']} {kinds(r)}")
    # NaN-ish quantity -> unknown too.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200,
                                "body": pre_body([{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": "two"}])},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_nan_qty_unknown", r["out"]["skip"] == "cart_qty_unknown" and "po" not in kinds(r))
    # qty 4 with Q 2 -> cart_qty_over (also split across two lines).
    for items in ([{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 4}],
                  [{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 2},
                   {"cart_item_id": "CI-2", "tcin": TCIN, "quantity": 2}]):
        r = node_run(ticket_js(q=2), [{"match": PRE_M, "status": 200, "body": pre_body(items)},
                                      {"match": PO_M, "status": 200, "body": ORDER_BODY}])
        check(f"a_qty_over[{len(items)}]", r["out"]["skip"] == "cart_qty_over" and kinds(r) == ["pre"]
              and r["out"]["pre"]["qty"] == 4, f"{r['out']['skip']} {r['out']['pre'].get('qty')}")
    # Empty cart (parsed 2xx, n=0) -> cart_empty.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200, "body": pre_body([])},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_empty_cart", r["out"]["skip"] == "cart_empty" and r["out"]["pre"]["parsed"] is True
          and r["out"]["pre"]["n"] == 0 and kinds(r) == ["pre"], str(r["out"]))
    # Foreign item -> skip + ids, no place-order.
    foreign = [{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 2},
               {"cart_item_id": "CI-F", "tcin": OTHER, "quantity": 1}]
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200, "body": pre_body(foreign)},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    ids = {(i["id"], i["tcin"]) for i in r["out"]["pre"]["items"]}
    check("a_foreign_skip_with_ids", r["out"]["skip"] == "foreign_cart_item" and kinds(r) == ["pre"]
          and ("CI-F", OTHER) in ids and ("CI-1", TCIN) in ids, f"{r['out']['skip']} {ids}")
    # A line without a tcin is not ours -> foreign.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200,
                                "body": pre_body([{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 1},
                                                  {"cart_item_id": "CI-X", "quantity": 1}])},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_tcinless_line_is_foreign", r["out"]["skip"] == "foreign_cart_item" and "po" not in kinds(r))
    # A numeric tcin equal to ours is ours (String normalisation).
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200,
                                "body": pre_body([{"cart_item_id": "CI-1", "tcin": int(TCIN), "quantity": 1}])},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_numeric_tcin_is_ours", r["out"]["skip"] == "" and kinds(r) == ["pre", "po"], str(kinds(r)))
    # pre 429 (FAST_SELLING body kept) -> no place-order.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 429, "body": FS_BODY},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_pre_429_no_po", r["out"]["skip"] == "pre_429" and kinds(r) == ["pre"]
          and "FAST_SELLING" in r["out"]["pre"]["body"], str(r["out"]))
    # pre fetch throws -> pre_0, no place-order.
    r = node_run(ticket_js(), [{"match": PRE_M, "throw": "net down", "status": 0},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_pre_throw_no_po", r["out"]["skip"] == "pre_0" and r["out"]["po"]["fired"] is False)
    # po_only -> one fetch, the checkout POST, never the ATC URL / pre.
    r = node_run(ticket_js("po_only"), [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                                        {"match": PO_M, "status": 429, "body": FS_BODY}])
    check("a_po_only_one_fetch", kinds(r) == ["po"] and r["out"]["po"]["status"] == 429
          and r["calls"][0]["method"] == "POST", str(kinds(r)))
    # place-order throws -> fired stays true, status 0.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                               {"match": PO_M, "throw": "reset", "status": 0}])
    check("a_po_throw_reported_fired", r["out"]["po"]["fired"] is True and r["out"]["po"]["status"] == 0)
    # Abort while pre is pending, then pre resolves 201 on an exact cart ->
    # NO place-order fetch; the read reported {s:'pre', aborted:true}.
    for first in (False, True):
        r = node_run(ticket_js(cvv="123", first=first, pi="PI-1234567890"),
                     [{"match": PRE_M, "status": 201, "body": pre_body(EXACT), "hang": True},
                      {"match": PUT_M, "status": 200, "body": "{}"},
                      {"match": PO_M, "status": 200, "body": ORDER_BODY}], abort=True)
        check(f"a_abort_during_pre_no_po[first={first}]",
              r["abortRead"] == {"s": "pre", "aborted": True} and kinds(r) == ["pre"]
              and r["out"]["skip"] == "aborted" and r["out"]["po"]["fired"] is False,
              f"{r['abortRead']} {kinds(r)} {r['out']['skip']}")
    # Abort while the CVV PUT is pending -> no place-order.
    r = node_run(ticket_js(cvv="123", first=True),
                 [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                  {"match": PUT_M, "status": 200, "body": "{}", "hang": True},
                  {"match": PO_M, "status": 200, "body": ORDER_BODY}], abort=True)
    check("a_abort_during_cvv_no_po", r["abortRead"] == {"s": "cvv", "aborted": True}
          and kinds(r) == ["pre", "put"] and r["out"]["skip"] == "aborted", f"{r['abortRead']} {kinds(r)}")
    # Abort requested while the place-order is pending -> NOT aborted (the POST
    # may be on the wire); the chain completes and reports the response.
    r = node_run(ticket_js(), [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                               {"match": PO_M, "status": 200, "body": ORDER_BODY, "hang": True}], abort=True)
    check("a_abort_during_po_refused", r["abortRead"] == {"s": "po", "aborted": False}
          and r["out"]["po"]["status"] == 200 and r["out"]["skip"] == "", f"{r['abortRead']} {r['out']}")
    # po_only aborted before the POST is impossible to observe (no await before
    # the fetch); pending po_only -> refused.
    r = node_run(ticket_js("po_only"), [{"match": PO_M, "status": 429, "body": FS_BODY, "hang": True}], abort=True)
    check("a_po_only_pending_refused", r["abortRead"] == {"s": "po", "aborted": False})
    # Stage key: non-enumerable while running; entry deleted after completion
    # and (R1 review) the container with it, so no page global survives; the
    # late read returns null (outcome unknown -> terminal).
    check("a_stage_key_non_enumerable", r["enumDuring"] is False and r["enumAfter"] is False
          and r["hasKeyAfter"] is False and r["hadKeyDuring"] is True, str(r))
    check("a_stage_entry_deleted", r["entryAfter"] is False and r["lateAbort"] is None, str(r))
    # Reactive CVV: po 400 -> one PUT -> one re-shoot; put failure -> no re-shoot.
    r = node_run(ticket_js(cvv="123", reactive=True),
                 [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                  {"match": PUT_M, "status": 200, "body": "{}"},
                  {"match": PO_M, "seq": [{"status": 400, "body": "{}"}, {"status": 200, "body": ORDER_BODY}]}])
    check("a_reactive_cvv_reshoot", kinds(r) == ["pre", "po", "put", "po"] and r["out"]["cvv"]["reshot"] is True
          and r["out"]["po"]["status"] == 200 and r["out"]["cvv"]["po1"] == 400, f"{kinds(r)} {r['out']['cvv']}")
    put_body = json.loads(r["calls"][2]["body"])
    check("a_reactive_put_uses_pre_ids", "PI-1234567890" in r["calls"][2]["url"]
          and put_body.get("cart_id") == "CART-1234567890" and put_body["card_details"]["cvv"] == "123")
    r = node_run(ticket_js(cvv="123", reactive=True),
                 [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                  {"match": PUT_M, "status": 500, "body": "{}"},
                  {"match": PO_M, "status": 400, "body": "{}"}])
    check("a_reactive_put_fail_no_reshoot", kinds(r) == ["pre", "po", "put"] and r["out"]["cvv"]["reshot"] is False)
    # Ledger cvv_put='ok' -> the executor renders both CVV constants false -> no PUT.
    r = node_run(ticket_js(cvv="123", first=False, reactive=False),
                 [{"match": PRE_M, "status": 200, "body": pre_body(EXACT)},
                  {"match": PUT_M, "status": 200, "body": "{}"},
                  {"match": PO_M, "status": 400, "body": "{}"}])
    check("a_cvv_constants_off_no_put", kinds(r) == ["pre", "po"], str(kinds(r)))
    # po_only CVV_FIRST uses the ledger PI id; without an id there is no PUT.
    r = node_run(ticket_js("po_only", cvv="123", first=True, pi="PI-1234567890"),
                 [{"match": PUT_M, "status": 200, "body": "{}"}, {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_po_only_cvv_first_ledger_pi", kinds(r) == ["put", "po"] and "PI-1234567890" in r["calls"][0]["url"])
    r = node_run(ticket_js("po_only", cvv="123", first=True, pi=""),
                 [{"match": PUT_M, "status": 200, "body": "{}"}, {"match": PO_M, "status": 200, "body": ORDER_BODY}])
    check("a_po_only_no_pi_no_put", kinds(r) == ["po"] and r["out"]["cvv"]["put"] == -1)


def test_a_python_primitive():
    """The real _api_checkout_ticket: CVV ledger, abort protocol outcomes, AC-1 flags."""
    c = Clock()
    # Ledger: a PUT happened -> cvv_put recorded -> next render has no PUT.
    ex = bare(c, cvv="123", cvv_required=True)
    L = {"tcin": TCIN, "tickets": 0, "cvv_put": "none", "pi_id": "PI-1234567890"}
    tab = TicketTab(ex, [t_prepo(429, FS_BODY, put=200), t_po(429, FS_BODY)], c)
    res = run(ex._api_checkout_ticket(tab, TCIN, 2, ex._ticket_headers_js(), "pre_po", L))
    js1 = tab.tickets[0][2]
    check("p_first_ticket_cvv_first", "const CVV_FIRST = true;" in js1 and "const CVV = '123';" in js1)
    check("p_ledger_records_put", L["cvv_put"] == "ok", L)
    run(ex._api_checkout_ticket(tab, TCIN, 2, ex._ticket_headers_js(), "po_only", L))
    js2 = tab.tickets[1][2]
    check("p_second_ticket_no_cvv_put", "const CVV_FIRST = false;" in js2
          and "const CVV_REACTIVE = false;" in js2)
    check("p_ticket_line_printed", "[WON_CART_DIRECT] ticket n=1 mode=pre_po pre=200 po=429" in run.last_out
          or "[WON_CART_DIRECT] ticket" in run.last_out, run.last_out[-300:])
    check("p_headers_strip_cookie_referer", "cookie" not in js1.lower().split("const pre_url")[0]
          and '"x-application-name": "web"' in js1 and "X-GyJwza5Z-a" in js1)
    check("p_unique_ticket_ids", re.search(r"const N = '(t\d+)'", js1).group(1)
          != re.search(r"const N = '(t\d+)'", js2).group(1))
    # Failed PUT -> 'failed', still no second PUT.
    L2 = {"tcin": TCIN, "tickets": 0, "cvv_put": "none"}
    tab = TicketTab(ex, [t_prepo(400, "{}", put=500), t_prepo(400, "{}")], c)
    run(ex._api_checkout_ticket(tab, TCIN, 2, "{}", "pre_po", L2))
    run(ex._api_checkout_ticket(tab, TCIN, 2, "{}", "pre_po", L2))
    check("p_failed_put_capped", L2["cvv_put"] == "failed"
          and "const CVV_FIRST = false;" in tab.tickets[1][2] and "const CVV_REACTIVE = false;" in tab.tickets[1][2])
    # Timeout + aborted read -> not fired, flags clear.
    ex = bare(c)
    tab = TicketTab(ex, [{"raise": asyncio.TimeoutError(), "abort_read": {"s": "pre", "aborted": True}}], c)
    res = run(ex._api_checkout_ticket(tab, TCIN, 2, "{}", "pre_po", {"tickets": 0}))
    check("p_timeout_aborted", res["skip"] == "ticket_timeout_aborted" and res["po"]["fired"] is False
          and tab.aborts == 1 and not ex._po_inflight and not ex._woncart_po_unresolved
          and not ex._po_ambiguous, f"{res} {ex._woncart_po_unresolved}")
    # Timeout + stage po -> fired, status 0, ambiguous + unresolved.
    for ab in ({"s": "po", "aborted": False}, None):
        ex = bare(c)
        tab = TicketTab(ex, [{"raise": RuntimeError("ws closed"), "abort_read": ab}], c)
        res = run(ex._api_checkout_ticket(tab, TCIN, 2, "{}", "pre_po", {"tickets": 0}))
        check(f"p_timeout_not_aborted_is_fired[{ab}]",
              res["po"]["fired"] is True and res["po"]["status"] == 0 and ex._po_ambiguous
              and ex._woncart_po_unresolved and not ex._po_inflight, str(res))
    # A malformed evaluate result (no po object) is an unknown outcome -> fired.
    for bad in (None, "x", {}, {"po": "nope"}):
        ex = bare(c)
        tab = TicketTab(ex, [{"out": bad}], c)
        res = run(ex._api_checkout_ticket(tab, TCIN, 2, "{}", "pre_po", {"tickets": 0}))
        check(f"p_bad_result_is_fired[{bad!r}]", res["skip"] == "ticket_bad_result"
              and res["po"]["fired"] is True and ex._woncart_po_unresolved and ex._po_ambiguous, str(res))
    # Received response -> unresolved cleared.
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(429, FS_BODY)], c)
    run(ex._api_checkout_ticket(tab, TCIN, 2, "{}", "pre_po", {"tickets": 0}))
    check("p_received_clears_unresolved", not ex._woncart_po_unresolved and not ex._po_inflight
          and not ex._po_ambiguous)
    # Bad input never evaluates.
    ex = bare(c)
    tab = TicketTab(ex, [], c)
    res = run(ex._api_checkout_ticket(tab, "12ab", 2, "{}", "pre_po", {}))
    check("p_bad_tcin_not_fired", res["skip"] == "ticket_bad_input" and not tab.tickets
          and res["po"]["fired"] is False and not ex._po_inflight)
    # Renderer validation.
    bad = 0
    for kw in ({"key": "__x"}, {"n": "1"}, {"tcin": "1'+x"}, {"mode": "atc"}, {"qty": 0}):
        args = dict(key=KEY, n="t1", tcin=TCIN, qty=2, mode="pre_po", headers_js="{}")
        args.update(kw)
        try:
            pe_mod.render_checkout_ticket_js(**args)
        except ValueError:
            bad += 1
    check("p_renderer_rejects_bad_input", bad == 5, bad)
    js = pe_mod.render_checkout_ticket_js(KEY, "t1", TCIN, 2, "pre_po", "not json", cvv="12x",
                                          cvv_first=True, pi_id="x'); alert(1); //", cart_id="C")
    check("p_renderer_sanitises", "const CVV = '';" in js and "const CVV_FIRST = false;" in js
          and "let piId = '';" in js and "let cartId = '';" in js
          and '{"x-application-name": "web"}' in js and "@@" not in js)
    # A stage key is created once per executor and matches the validator.
    ex = bare(c)
    k1 = ex._ticket_stage_key()
    check("p_stage_key_stable", k1 == ex._ticket_stage_key() and re.fullmatch(r"__[0-9a-f]{12}", k1))


# ───────────────── (b) QG in the fast-lane JS + its caller ────────────────────

FL_HARNESS = r"""
const scenario = %s;
const calls = [];
globalThis.fetch = async (url, opts) => {
  calls.push(String(url).split('?')[0]);
  for (const r of scenario) {
    if (String(url).includes(r.match)) return {status: r.status, text: async () => r.body || ''};
  }
  throw new Error('no rule ' + url);
};
(async () => { const out = await (%s); console.log(JSON.stringify({out, calls})); })()
  .catch(e => console.log(JSON.stringify({error: String(e)})));
"""


class _JsTab:
    def __init__(self):
        self.js = None

    async def evaluate(self, js, await_promise=False):
        self.js = js
        return {"atc": {"status": 401}, "pre": {}, "po": {}, "skip": "atc_401"}


def fast_lane_js(**flags):
    c = Clock()
    ex = bare(c)
    tab = _JsTab()
    with env(**flags):
        run(ex._api_fast_lane(tab, TCIN, 2, json.dumps({"X-GyJwza5Z-a": "tok"})))
    return tab.js


def fl_node(js, pre_items):
    body = json.dumps({"cart_id": "CART-1234567890", "cart_items": pre_items,
                       "payment_instructions": [{"payment_instruction_id": "PI-9", "payment_type": "CARD"}]})
    scen = [{"match": "cart_items", "status": 201,
             "body": json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2})},
            {"match": "pre_checkout", "status": 200, "body": body},
            {"match": "v1/checkout", "status": 200, "body": ORDER_BODY}]
    src = FL_HARNESS % (json.dumps(scen), js)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8", dir=_TMP) as f:
        f.write(src)
        path = f.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    if "error" in res:
        raise AssertionError(res["error"])
    return res


def test_b_qg_fast_lane():
    off = fast_lane_js()
    check("b_qg_off_no_insertion", "_qp" not in off and "cart_qty_over" not in off)
    for flag in ("TARGET_FASTLANE_QTY_GUARD", "TARGET_WONCART_DIRECT", "TARGET_HELD_CART_REENTRY"):
        on = fast_lane_js(**{flag: "1"})
        check(f"b_qg_forced_on_by[{flag}]", "cart_qty_over" in on and "_qp" in on)
    on = fast_lane_js(TARGET_FASTLANE_QTY_GUARD="1")
    check("b_qg_only_adds_lines", all(line in on.split("\n") for line in off.split("\n")))
    if not NODE:
        check("b_node_available", False, "node not on PATH — QG node tests cannot run")
        return
    null_item = [None, {"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 2}]
    r = fl_node(on, null_item)
    check("b_qg_null_item_keeps_pi", r["out"]["pre"]["pi"] and r["out"]["pre"]["pi"][0]["id"] == "PI-9"
          and r["out"]["pre"]["qty"] == 2 and r["out"]["po"]["status"] == 200, str(r["out"]["pre"]))
    r = fl_node(on, [{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 4}])
    check("b_qg_qty4_over_no_po", r["out"]["skip"] == "cart_qty_over" and r["out"]["po"]["fired"] is False
          and not any(u.endswith("/v1/checkout") for u in r["calls"]), str(r))
    r = fl_node(on, [{"cart_item_id": "CI-1", "tcin": TCIN}])
    check("b_qg_unknown_qty_fail_open", r["out"]["pre"]["qty"] is None and r["out"]["po"]["status"] == 200)
    r = fl_node(off, [{"cart_item_id": "CI-1", "tcin": TCIN, "quantity": 4}])
    check("b_qg_off_today_places", r["out"]["po"]["status"] == 200 and "qty" not in r["out"]["pre"])
    # Every flag combination parses.
    for combo in ({}, {"TARGET_ATC_BYTEMATCH": "1"}, {"TARGET_FASTLANE_QTY_GUARD": "1", "TARGET_ATC_BYTEMATCH": "1"}):
        js = fast_lane_js(**combo)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8", dir=_TMP) as f:
            f.write(js)
        p = subprocess.run([NODE, "--check", f.name], capture_output=True, text=True, timeout=30)
        check(f"b_fast_lane_js_parses[{sorted(combo)}]", p.returncode == 0, p.stderr[:200])


# ───────────── impl-level harness (the real _execute_purchase_impl) ───────────

class LegacyReached(Exception):
    pass


class ImplTab:
    def __init__(self):
        self.url = "https://www.target.com/account"
        self.handlers = {}
        self.enabled_domains = []
        self.gets = []

    async def get(self, url):
        self.gets.append(url)

    async def send(self, *a, **k):
        return None

    async def evaluate(self, js, await_promise=False):
        return None


def impl_ex(clock, fl0, loop_result=None, cvv="", cvv_required=False):
    ex = bare(clock, cvv=cvv, cvv_required=cvv_required)
    tab = ImplTab()
    ex._tcin_throttle_until = {}
    ex._cdp_continued_ids = set()
    ex._main_tab_interceptor_active = False
    ex._pdp_qty_cache = {}
    ex._pdp_qty_ttl = 1800.0
    ex._shot_ttl_refresh_on = False
    ex._harvest_cfg = {}
    ex._cvv_modal_seen = False
    ex.statuses = []

    def _notify(tcin, status, data=None):
        ex.statuses.append(status)
        if status == "checking_out":
            raise LegacyReached("legacy checkout path reached")

    ex._notify_status = _notify

    async def _get_page():
        return tab

    ex.session_manager.get_page = _get_page
    ex.session_manager.browser = None

    async def _alive(t):
        return True, "ok"

    async def _setup(t, persistent=False):
        return None

    ex._cdp_alive_probe = _alive
    ex._setup_cdp_fetch_interceptor = _setup
    ex._consume_fresh_capture = lambda: False
    ex.fl_calls = []

    async def _fl(t, tcin, qty, hdrs):
        ex.fl_calls.append(qty)
        return json.loads(json.dumps(fl0))

    ex._api_fast_lane = _fl
    ex.loop_calls = []

    async def _loop(t, tcin, qty, fl, start_time, entry="first"):
        ex.loop_calls.append((tcin, qty, entry))
        v, r = loop_result or ("done", {"success": False, "tcin": tcin, "reason": "won_cart_held"})
        if v == "placed":
            ex._fastlane_placed = True
            ex._api_order_id = "OID-LOOP"
            ex._api_confirmation_url = "https://www.target.com/checkout/confirmation?orderId=OID-LOOP"
        return v, r

    ex._won_cart_ticket_loop = _loop
    return ex, tab


FL_PRE429 = {"atc": {"status": 201, "body": "", "cart_items": [{"tcin": TCIN, "quantity": 2}]},
             "pre": {"status": 429, "body": FS_BODY}, "po": {"status": 0, "body": "", "fired": False},
             "skip": "pre_429"}
FL_PO_FS = {"atc": {"status": 201, "body": "", "cart_items": [{"tcin": TCIN, "quantity": 2}]},
            "pre": {"status": 200, "n": 1, "tcins": [TCIN], "qty": 2, "pi": []},
            "po": {"status": 429, "body": FS_BODY, "fired": True}, "skip": ""}
FL_PO_0 = dict(FL_PO_FS, po={"status": 0, "body": "", "fired": True})
FL_PO_424 = dict(FL_PO_FS, po={"status": 424, "body": "{}", "fired": True})
FL_QTY_OVER = dict(FL_PRE429, pre={"status": 200, "n": 1, "tcins": [TCIN], "qty": 4}, skip="cart_qty_over")

IMPL_ENV = dict(TARGET_API_PLACE_ORDER="true")


def run_impl(ex, extra_env=None):
    e = dict(IMPL_ENV)
    e.update(extra_env or {})
    with in_tmp_cwd(), env(**e):
        return run(ex._execute_purchase_impl(TCIN, quantity=2))


def test_c_call_site():
    c = Clock()
    # Armed + (A) pre_429 -> loop -> placed -> success dict with order id + qty.
    ex, tab = impl_ex(c, FL_PRE429, loop_result=("placed", None))
    r = run_impl(ex, ARMED)
    check("c_site_placed_success", r.get("success") is True and r.get("order_id") == "OID-LOOP"
          and r.get("quantity") == 2 and ex.loop_calls == [(TCIN, 2, "first")]
          and "checking_out" not in ex.statuses and ex.session_manager.saved == 1, f"{r} {ex.statuses}")
    check("c_site_placed_notifies_purchased", "purchased" in ex.statuses, ex.statuses)
    # save_session_state raising never turns the order into a failure.
    ex, tab = impl_ex(c, FL_PRE429, loop_result=("placed", None))
    ex.session_manager.save_raises = RuntimeError("websocket closed")
    r = run_impl(ex, ARMED)
    check("c_site_placed_save_error_still_success", r.get("success") is True and r.get("order_id") == "OID-LOOP", r)
    # (B) po 429 FS -> loop; its final result is returned as-is (no fallthrough).
    ex, tab = impl_ex(c, FL_PO_FS, loop_result=("done", {"success": False, "tcin": TCIN, "reason": "won_cart_held"}))
    r = run_impl(ex, ARMED)
    check("c_site_b_eligible_returns_loop_result", r.get("reason") == "won_cart_held" and ex.loop_calls
          and "checking_out" not in ex.statuses, f"{r} {ex.statuses}")
    ex, tab = impl_ex(c, FL_PO_FS, loop_result=("terminal", {"success": False, "tcin": TCIN,
                                                            "reason": "checkout_navigation_failed",
                                                            "ambiguous_commit": True}))
    r = run_impl(ex, ARMED)
    check("c_site_terminal_returned", r.get("ambiguous_commit") is True and "checking_out" not in ex.statuses)
    # Not eligible / not armed -> the legacy path runs (unchanged).
    cases = [
        ("flag_off", FL_PRE429, {}, {}),
        ("ac1_off", FL_PRE429, {"TARGET_WONCART_DIRECT": "1"}, {}),
        ("po_status_0", FL_PO_0, ARMED, {}),
        ("po_424_rf", FL_PO_424, ARMED, {}),
        ("cvv_required_no_digits", FL_PRE429, ARMED, {"cvv_required": True}),
    ]
    for label, fl0, e, kw in cases:
        ex, tab = impl_ex(c, fl0, **kw)
        r = run_impl(ex, e)
        if label == "po_status_0":
            ok = r.get("reason") == "checkout_navigation_failed" and not ex.loop_calls
        elif label == "cvv_required_no_digits":
            ok = not ex.fl_calls and not ex.loop_calls       # the fast lane itself is off
        else:
            ok = not ex.loop_calls and "checking_out" in ex.statuses
        check(f"c_site_not_eligible[{label}]", ok, f"{r} loop={ex.loop_calls} st={ex.statuses}")
    ex, tab = impl_ex(c, FL_PRE429)
    ex.test_mode = True
    r = run_impl(ex, ARMED)
    check("c_site_test_mode_never_loops", not ex.loop_calls, ex.loop_calls)
    # Refusal is loud once and writes one error_log line.
    ex, tab = impl_ex(c, FL_PRE429)
    with in_tmp_cwd() as d, env(TARGET_API_PLACE_ORDER="true", TARGET_WONCART_DIRECT="1"):
        run(ex._execute_purchase_impl(TCIN, quantity=2))
        out1 = run.last_out
        run(ex._execute_purchase_impl(TCIN, quantity=2))
        out2 = run.last_out
        log = Path(d, "logs", "error_log.txt").read_text(encoding="utf-8")
    check("c_refusal_logged_once", "REFUSING to arm" in out1 and "REFUSING to arm" not in out2
          and log.count("REFUSING to arm") == 1, log[-300:])
    # CVV-required with digits -> eligible.
    ex, tab = impl_ex(c, FL_PRE429, cvv="123", cvv_required=True)
    r = run_impl(ex, ARMED)
    check("c_site_cvv_with_digits_loops", ex.loop_calls and r.get("reason") == "won_cart_held", r)
    # QG caller: cart_qty_over -> bounded delete of OUR line -> cart_qty_cleared,
    # never the legacy path; failed delete -> cart_qty_stuck.
    for ok_del, want in ((True, "cart_qty_cleared"), (False, "cart_qty_stuck")):
        ex, tab = impl_ex(c, FL_QTY_OVER)
        ex.delete_result = (ok_del, 1 if ok_del else 0)
        r = run_impl(ex, {"TARGET_FASTLANE_QTY_GUARD": "1"})
        check(f"c_qg_caller[{want}]", r.get("reason") == want and ex.deletes
              and ex.deletes[0]["only"] == TCIN and ex.deletes[0]["keep"] is None
              and ex.deletes[0]["budget"] <= 15.0 and "checking_out" not in ex.statuses
              and not ex.loop_calls, f"{r} {ex.deletes}")
    # S-B1: an order already placed this purchase never reaches the fast lane.
    ex, tab = impl_ex(c, FL_PRE429)
    orig = ex._consume_fresh_capture

    def _mark():
        ex._fastlane_placed = True
        ex._api_order_id = "OID-EARLY"
        return orig()

    ex._consume_fresh_capture = _mark
    r = run_impl(ex, ARMED)
    check("c_placed_short_circuit", r.get("success") is True and r.get("order_id") == "OID-EARLY"
          and not ex.fl_calls, f"{r} fl={ex.fl_calls}")
    # Generic handler guard: an exception after the order was placed -> success,
    # no cart clear; an unresolved won-cart place-order -> terminal + ambiguous.
    for label, setup, want in (
        ("placed", lambda e: (setattr(e, "_fastlane_placed", True), setattr(e, "_api_order_id", "OID-G")), "success"),
        ("unresolved", lambda e: setattr(e, "_woncart_po_unresolved", True), "terminal"),
    ):
        ex, tab = impl_ex(c, FL_PRE429)

        async def _boom(t, tcin, qty, fl, start_time, entry="first", _e=ex, _s=setup):
            _s(_e)
            raise RuntimeError("boom after the POST")

        ex._won_cart_ticket_loop = _boom
        clears = []

        async def _clear(t):
            clears.append(t)
            return True

        ex._clear_cart = _clear
        r = run_impl(ex, ARMED)
        if want == "success":
            ok = r.get("success") is True and r.get("order_id") == "OID-G"
        else:
            ok = (r.get("reason") == "checkout_navigation_failed" and r.get("ambiguous_commit") is True
                  and r.get("success") is False)
        check(f"c_generic_handler_guard[{label}]", ok and not clears, f"{r} clears={len(clears)}")
    # Flag off: the generic handler is unchanged (reason 'exception').
    ex, tab = impl_ex(c, FL_PRE429)

    async def _boom2(*a, **k):
        ex._fastlane_placed = True
        raise RuntimeError("boom")

    ex._consume_fresh_capture = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    r = run_impl(ex, {})
    check("c_generic_handler_flag_off_unchanged", r.get("reason") == "exception", r)


def test_c_hang_branch_placed():
    """execute_purchase's 140 s hang branch reports success when the order is
    already placed (flag on) and is unchanged with the flag off."""
    c = Clock()
    for armed in (True, False):
        ex = bare(c)
        ex._page_lock = asyncio.Lock() if False else None
        ex._purchase_timeout_ctx = None
        ex._note_atc_gate_outcome = lambda t, r: None

        async def _impl(tcin, quantity=1, _e=ex):
            _e._fastlane_placed = True
            _e._api_order_id = "OID-H"
            await REAL_ASYNCIO.sleep(5)

        ex._execute_purchase_impl = _impl
        shim = types.SimpleNamespace(**{n: getattr(REAL_ASYNCIO, n) for n in dir(REAL_ASYNCIO) if not n.startswith("__")})
        shim.timeout = lambda s: REAL_ASYNCIO.timeout(0.05)
        saved = pe_mod.asyncio
        pe_mod.asyncio = shim
        try:
            async def go():
                ex._page_lock = REAL_ASYNCIO.Lock()
                return await ex.execute_purchase(TCIN, quantity=2)
            with env(**(ARMED if armed else {})):
                r = run(go())
        finally:
            pe_mod.asyncio = saved
        if armed:
            check("c_hang_after_placed_reports_success", r.get("success") is True and r.get("order_id") == "OID-H"
                  and r.get("quantity") == 2, r)
        else:
            check("c_hang_flag_off_unchanged", r.get("reason") == "purchase_impl_hang", r)


# ───────────────────────────── (c) the loop ──────────────────────────────────

def loop_run(ex, tab, clock, fl0, entry="first", stop_after=None, **flags):
    e = dict(ARMED)
    e.update(flags)
    with in_tmp_cwd(), env(**e), fake_time(clock, stop_after) as shim:
        r = run(ex._won_cart_ticket_loop(tab, TCIN, 2, fl0, clock.t, entry=entry))
    return r, shim


def ticket_offsets(tab, t0):
    return [round(t - t0, 3) for t, _, _ in tab.tickets]


def test_c_loop_core():
    # [pre FS, pre 201 -> po FS (verified), po_only 200] -> placed.
    c = Clock()
    t0 = c.t
    ex = bare(c)
    warms = []

    async def _warm(*a, **k):
        warms.append(("warm", a, k))
        return True

    ex.warm_shape_headers = _warm
    ex._consume_fresh_capture = lambda: warms.append(("consume",)) or False
    ex._refresh_on_tab = _warm
    tab = TicketTab(ex, [t_pre(429, FS_BODY), t_prepo(429, FS_BODY, rej=(429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION")),
                         t_po(200, ORDER_BODY)], c)
    (v, r), shim = loop_run(ex, tab, c, FL_PRE429)
    check("l_never_warms_or_consumes", warms == [], warms)
    check("l_quiet_during_tickets", all("const MODE" in js for _, _, js in tab.tickets))
    modes = [m for _, m, _ in tab.tickets]
    check("l_placed_flow", v == "placed" and r is None and ex._fastlane_placed
          and ex._api_order_id == "OID-777" and modes == ["pre_po", "pre_po", "po_only"], f"{v} {r} {modes}")
    check("l_no_fs_throttle_during_loop", ex.fs_notes == [], ex.fs_notes)
    check("l_quiet_cleared_after", ex._woncart_active_until == 0.0)
    check("l_ride_started", len(ex.ride_calls) == 1 and abs(ex.ride_calls[0] - (t0 + 300 - 30)) < 1e-6, ex.ride_calls)
    check("l_start_end_lines", "[WON_CART_DIRECT] start entry=first" in run.last_out
          and "[WON_CART_DIRECT] end reason=placed" in run.last_out, run.last_out[-400:])
    check("l_no_chain_done_line", "[FAST_LANE] chain done" not in run.last_out
          and "legacy path continues" not in run.last_out and "falling back to nav+DOM" not in run.last_out)
    check("l_fs_quiet_rule_for_next_race", ex._fast_selling_until > c.t - 60, ex._fast_selling_until)
    # Gaps 5, 15, then 45 +/- 3 (pre 429 without FS, live True).
    c = Clock()
    t0 = c.t
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(429)] * 10, c)
    (v, r), shim = loop_run(ex, tab, c, dict(FL_PRE429, pre={"status": 429, "body": ""}))
    offs = ticket_offsets(tab, t0)
    check("l_gap_schedule", len(offs) >= 3 and offs[0] == 5.0 and offs[1] == 20.0
          and 42.0 <= offs[2] - offs[1] <= 48.0, str(offs))
    check("l_sleeps_recorded", shim.sleeps[:2] == [5.0, 15.0] and all(42 <= s <= 48 for s in shim.sleeps[2:]),
          str(shim.sleeps))
    check("l_call_cap_exit", r.get("won_cart_exit") == "call_cap" and offs[-1] <= 120.0
          and r.get("reason") == "checkout_busy_retryable", f"{r} {offs}")
    check("l_call_cap_clears_when_time_allows", ex.deletes and ex.deletes[-1]["only"] is None
          and ex.deletes[-1]["keep"] is None, ex.deletes)
    check("l_ride_trimmed_after_exit", ex._won_cart_ride_until <= c.t + 20.0 + 1e-6, ex._won_cart_ride_until - c.t)
    # FS seen in fl0 -> the first gap is measured from the FS.
    c = Clock()
    t0 = c.t
    ex = bare(c)
    tab = TicketTab(ex, [t_po(200, ORDER_BODY)], c)
    with env(TARGET_WONCART_SCHEDULE_S="0"):
        (v, r), _ = loop_run(ex, tab, c, FL_PO_FS)
    check("l_no_probes_steady_from_fs", v == "placed" and 42.0 <= ticket_offsets(tab, t0)[0] <= 48.0,
          ticket_offsets(tab, t0))
    # fl0 with a verified pre (08-04 shape) -> first ticket po_only.
    c = Clock()
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    tab = TicketTab(ex, [t_po(429, FS_BODY)] * 5, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS)
    check("l_verified_fl0_po_only", tab.tickets and tab.tickets[0][1] == "po_only"
          and all(m == "po_only" for _, m, _ in tab.tickets), [m for _, m, _ in tab.tickets])
    # A fl0 pre with an unknown qty is NOT verified.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(429)], c)
    loop_run(ex, tab, c, dict(FL_PO_FS, pre=dict(FL_PO_FS["pre"], qty=None)), TARGET_WONCART_MAX_TICKETS="1")
    check("l_unknown_qty_fl0_pre_po", tab.tickets and tab.tickets[0][1] == "pre_po")
    # po status 0 -> terminal, ambiguous, no delete, marker dropped.
    c = Clock()
    ex = bare(c)
    ex._held_cart = {"tcin": TCIN, "tickets": 0, "sched_used": 2, "verified": True, "last_ticket_ts": c.t - 100,
                     "fs_seen_ts": 0.0, "cvv_put": "none", "created": c.t - 100}
    tab = TicketTab(ex, [t_po(0)], c)
    (v, r), _ = loop_run(ex, tab, c, None, entry="held", TARGET_HELD_CART_REENTRY="1")
    check("l_po0_terminal", v == "terminal" and r.get("ambiguous_commit") is True
          and r.get("reason") == "checkout_navigation_failed" and not ex.deletes
          and ex._held_cart is None and ex._po_ambiguous, f"{v} {r} {ex.deletes}")
    # _log_fs_ticket raising after a po 200 -> still placed with the order id.
    c = Clock()
    ex = bare(c)

    def _raise(*a, **k):
        raise RuntimeError("log broke")

    ex._log_fs_ticket = _raise
    tab = TicketTab(ex, [t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("l_log_raise_still_placed", v == "placed" and ex._api_order_id == "OID-777" and ex._fastlane_placed)
    # ... and a raising log on a non-placed ticket does not stop the loop.
    c = Clock()
    ex = bare(c)
    ex._log_fs_ticket = _raise
    tab = TicketTab(ex, [t_pre(429), t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("l_log_raise_loop_continues", v == "placed" and len(tab.tickets) == 2)
    # [FS_TICKET] line when armed.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(429, FS_BODY), t_prepo(200, ORDER_BODY)], c)
    loop_run(ex, tab, c, FL_PRE429, TARGET_FS_TICKET_LOG="1")
    check("l_fs_ticket_lines", run.last_out.count("[FS_TICKET]") == 2 and "cls=sched" in run.last_out
          and "layer=pre" in run.last_out and "layer=po" in run.last_out, run.last_out[-500:])


def test_c_loop_deadlines():
    # Ride flag 0 -> exec start + 140 (mgr 150-15) caps the last start.
    for label, flags, ride_ret in (("ride_off", {"TARGET_WON_CART_RIDE": "0"}, None),
                                   ("extend_0", {}, 0.0)):
        c = Clock()
        t0 = c.t
        ex = bare(c)
        if ride_ret is not None:
            ex.ride_return = ride_ret
        tab = TicketTab(ex, [t_pre(429)] * 20, c)
        with env(TARGET_WONCART_CALL_MAX_S="280"):
            (v, r), _ = loop_run(ex, tab, c, FL_PRE429, **flags)
        offs = ticket_offsets(tab, t0)
        check(f"d_cap_140[{label}]", offs and max(offs) <= 140 - 45 + 1e-6
              and r.get("won_cart_exit") == "budget_spent", f"{offs} {r}")
        if label == "ride_off":
            check("d_ride_off_never_extends", ex.ride_calls == [], ex.ride_calls)
    # Manager submitted 200 s before the executor started -> mgr cap honoured.
    c = Clock()
    t0 = c.t
    ex = bare(c)
    ex._mgr_submit_ts = t0 - 200
    tab = TicketTab(ex, [t_pre(429)] * 20, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    offs = ticket_offsets(tab, t0)
    check("d_mgr_cap", offs == [5.0, 20.0] and r.get("won_cart_exit") == "budget_spent"
          and (t0 - 200 + 300 - 15 - 45) >= t0 + offs[-1], f"{offs} {r}")
    # 60 s were left before the deadline -> the bounded whole-cart clear ran.
    check("d_clear_when_time_left", ex.deletes == [{"only": None, "keep": None, "budget": 15.0}], ex.deletes)
    # < 20 s before the deadline -> the cart is left alone.
    c = Clock()
    ex = bare(c)
    with in_tmp_cwd(), fake_time(c):
        res = run(ex._woncart_exit(TicketTab(ex, [], c), TCIN, {"tcin": TCIN}, {"reason": "budget_spent",
                                                                               "dl": c.t + 19.0}, c.t, False))
    check("d_no_clear_near_deadline", ex.deletes == [] and res.get("reason") == "checkout_busy_retryable"
          and "not clearing the cart" in run.last_out, f"{ex.deletes} {res}")
    # Headroom knob is clamped to >= 34 s.
    check("d_headroom_clamp", pe_mod.woncart_cfg({"TARGET_WONCART_HEADROOM_S": "5"})["headroom_s"] == 34.0)


def test_c_loop_exits():
    # pre 401 x1 -> continue; x3 -> hold (WC-3), no clear.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(401), t_pre(429), t_pre(401), t_pre(401), t_pre(401), t_pre(429)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1", TARGET_WONCART_CALL_MAX_S="280")
    check("e_pre401_streak_holds", len(tab.tickets) == 5 and r.get("reason") == "won_cart_held"
          and r.get("won_cart_exit") == "pre_401_streak" and not ex.deletes
          and isinstance(ex._held_cart, dict) and ex._held_cart["tickets"] == 5, f"{len(tab.tickets)} {r}")
    check("e_hold_keeps_ride_deadline", ex._won_cart_ride_until > c.t + 20.0)
    # Foreign mid-loop -> only non-T ids deleted (real _delete_cart_items), then continue.
    c = Clock()
    ex = bare(c)
    del ex._delete_cart_items          # use the real bounded delete
    cart = [{"id": "CI-1", "tcin": TCIN, "qty": 2}, {"id": "CI-F1", "tcin": OTHER, "qty": 1},
            {"id": "CI-F2", "tcin": "", "qty": 1}]
    tab = TicketTab(ex, [t_skip("foreign_cart_item"), t_prepo(200, ORDER_BODY)], c, cart_items=cart)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_foreign_selective_delete", tab.deleted == ["CI-F1", "CI-F2"] and v == "placed"
          and len(tab.tickets) == 2 and tab.tickets[1][1] == "pre_po", f"{tab.deleted} {v}")
    # Foreign but nothing deletable -> foreign_stuck.
    c = Clock()
    ex = bare(c)
    ex.delete_result = (True, 0)
    tab = TicketTab(ex, [t_skip("foreign_cart_item")], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_foreign_stuck", r.get("won_cart_exit") == "foreign_stuck" and ex.deletes[0]["keep"] == TCIN, r)
    # cart_empty (parsed 2xx, n=0) -> checkout_busy_retryable, no delete.
    c = Clock()
    ex = bare(c)
    ex._held_cart = {"tcin": TCIN}
    tab = TicketTab(ex, [t_skip("cart_empty", n=0, tcins=[])], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("e_cart_evicted", r.get("reason") == "checkout_busy_retryable" and r.get("won_cart_exit") == "cart_evicted"
          and not ex.deletes and ex._held_cart is None, f"{r} {ex.deletes}")
    # cart_empty without a parsed 2xx is NOT eviction.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_skip("cart_empty", parsed=False)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_unparsed_empty_not_evicted", r.get("won_cart_exit", "").startswith("unexpected_"), r)
    # qty over -> bounded delete of our line -> cart_qty_cleared.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_skip("cart_qty_over", qty=4)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_qty_over", r.get("reason") == "cart_qty_cleared" and ex.deletes == [
        {"only": TCIN, "keep": None, "budget": 15.0}], f"{r} {ex.deletes}")
    # unparsed / unknown qty x3 -> pre_unreadable.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_skip("pre_unparsed", parsed=False), t_skip("cart_qty_unknown", qty=None),
                         t_skip("pre_unparsed", parsed=False)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("e_pre_unreadable", r.get("won_cart_exit") == "pre_unreadable" and r.get("reason") == "won_cart_held"
          and len(tab.tickets) == 3, r)
    # live False -> 1 tail ticket, then won_cart_held.
    c = Clock()
    ex = bare(c)
    ex.probe = {"live": False}
    tab = TicketTab(ex, [t_pre(429)] * 5, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("e_oos_tail", len(tab.tickets) == 1 and r.get("reason") == "won_cart_held"
          and r.get("won_cart_exit") == "tail_spent", f"{len(tab.tickets)} {r}")
    # Tail 0 -> no ticket at all once OOS.
    c = Clock()
    ex = bare(c)
    ex.probe = {"live": False}
    tab = TicketTab(ex, [t_pre(429)], c)
    loop_run(ex, tab, c, FL_PRE429, TARGET_WONCART_OOS_TAIL_TICKETS="0")
    check("e_oos_tail_zero", len(tab.tickets) == 0)
    # Probe flips False during the sleep -> the fire-time read counts the tail.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(429)] * 5, c)
    reads = []

    def _probe(t):
        reads.append(c.t)
        return {"live": len(reads) <= 1}

    ex._stock_live_fn = _probe
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_fire_time_probe", len(tab.tickets) == 1 and r.get("won_cart_exit") == "tail_spent", f"{len(tab.tickets)} {r}")
    # Held entry with live None -> 0 tickets.
    c = Clock()
    ex = bare(c)
    ex.probe = {"live": None}
    ex._held_cart = {"tcin": TCIN, "tickets": 2, "sched_used": 2, "verified": True,
                     "last_ticket_ts": c.t - 60, "fs_seen_ts": 0.0, "cvv_put": "none", "created": c.t - 60}
    tab = TicketTab(ex, [t_po(429)], c)
    (v, r), _ = loop_run(ex, tab, c, None, entry="held", TARGET_HELD_CART_REENTRY="1")
    check("e_held_probe_unknown", len(tab.tickets) == 0 and r.get("won_cart_exit") == "probe_unknown"
          and r.get("reason") == "won_cart_held", r)
    # First entry with live None keeps its schedule.
    c = Clock()
    ex = bare(c)
    ex.probe = {"live": None}
    tab = TicketTab(ex, [t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_first_probe_unknown_fires", v == "placed" and len(tab.tickets) == 1)
    # Ledger cap across two calls -> retire + bounded delete + marker dropped.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(429)] * 10, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1",
                         TARGET_WONCART_MAX_TICKETS="3", TARGET_WONCART_CALL_MAX_S="20")
    first_n = len(tab.tickets)
    held = ex._held_cart
    check("e_cap_call1_held", first_n == 2 and r.get("reason") == "won_cart_held" and held["tickets"] == 2, f"{first_n} {r}")
    c.t += 30.0
    ex._execute_started_at = c.t
    ex._mgr_submit_ts = c.t
    ex.ride_return = c.t + 290
    (v, r), _ = loop_run(ex, tab, c, None, entry="held", TARGET_HELD_CART_REENTRY="1",
                         TARGET_WONCART_MAX_TICKETS="3", TARGET_WONCART_CALL_MAX_S="20")
    check("e_cap_call2_retired", len(tab.tickets) == 3 and r.get("reason") == "won_cart_retired"
          and ex.deletes and ex.deletes[-1]["only"] == TCIN and ex._held_cart is None
          and held["sched_used"] == 2, f"{len(tab.tickets)} {r} {ex.deletes}")
    # Yield-fleet: another armed TCIN is live and the schedule is spent.
    c = Clock()
    ex = bare(c)
    ex._other_live_fn = lambda t: [OTHER]
    tab = TicketTab(ex, [t_pre(429)] * 5, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("e_yield_fleet", len(tab.tickets) == 2 and r.get("won_cart_exit") == "yield_fleet"
          and r.get("reason") == "won_cart_held", f"{len(tab.tickets)} {r}")
    c = Clock()
    ex = bare(c)
    ex._other_live_fn = lambda t: [OTHER]
    tab = TicketTab(ex, [t_pre(429)] * 5, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)          # WC-3 off -> yield inert
    check("e_yield_needs_wc3", len(tab.tickets) >= 3, len(tab.tickets))
    # A stale 424 key is never attributed to a later 429; nor a pre-loop FS key.
    c = Clock()
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    ex._checkout_rejected = True
    tab = TicketTab(ex, [t_prepo(424, "{}", rej=(424, "RESERVATION_FAILURE")), t_prepo(429, "{}"),
                         t_prepo(200, ORDER_BODY)], c)
    seen_rej = []
    orig_apply = ex._apply_fast_lane_result

    def _spy(fl, tcin, st, note_fs=True, ticket=False):
        seen_rej.append((ex._checkout_reject_status, ex._checkout_reject_reason))
        return orig_apply(fl, tcin, st, note_fs=note_fs, ticket=ticket)

    ex._apply_fast_lane_result = _spy
    (v, r), _ = loop_run(ex, tab, c, dict(FL_PRE429, pre={"status": 429, "body": ""}))
    check("e_stale_key_not_attributed", v == "placed" and seen_rej[1] == (0, "")
          and seen_rej[0] == (424, "RESERVATION_FAILURE"), seen_rej)
    check("e_stale_fs_not_recorded", ex._fast_selling_until == 0.0, ex._fast_selling_until)
    # 424 -> the next ticket re-verifies (pre_po); its 429 passed the gate -> po_only.
    check("e_424_forces_reverify", [m for _, m, _ in tab.tickets] == ["pre_po", "pre_po", "po_only"],
          [m for _, m, _ in tab.tickets])
    # po 401 x3 -> po_401_streak; po 500 -> po_500; 400 w/o key -> re-verify.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(400, "{}"), t_prepo(401), t_prepo(401), t_prepo(401)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_WONCART_CALL_MAX_S="280")
    check("e_po401_streak", r.get("won_cart_exit") == "po_401_streak" and len(tab.tickets) == 4
          and [m for _, m, _ in tab.tickets][1] == "pre_po", f"{r} {[m for _, m, _ in tab.tickets]}")
    # R3: a received 5xx is unresolved (test_r3_po_5xx_unresolved); another
    # definitive rejection still breaks with its status.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(409, "{}")], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_po_other_breaks", r.get("won_cart_exit") == "po_409" and v == "done"
          and not r.get("ambiguous_commit"), r)
    # 400 with MISSING_CREDIT_CARD_CVV -> continue (latched); other keyed 400 -> break.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(400, "{}", rej=(400, "MISSING_CREDIT_CARD_CVV")),
                         t_prepo(400, "{}", rej=(400, "SOME_OTHER_KEY"))], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_400_keys", len(tab.tickets) == 2 and r.get("won_cart_exit") == "po_400" and ex._cvv_required,
          f"{len(tab.tickets)} {r}")
    # Ticket timeout aborted -> continue with pre_po.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [{"raise": asyncio.TimeoutError(), "abort_read": {"s": "pre", "aborted": True}},
                         t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_aborted_continues", v == "placed" and [m for _, m, _ in tab.tickets] == ["pre_po", "pre_po"],
          f"{v} {[m for _, m, _ in tab.tickets]}")
    # A verified cart whose ticket was aborted loses its verification.
    c = Clock()
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    tab = TicketTab(ex, [{"raise": asyncio.TimeoutError(), "abort_read": {"s": "init", "aborted": True}},
                         t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS)
    check("e_aborted_unverifies", v == "placed" and [m for _, m, _ in tab.tickets] == ["po_only", "pre_po"],
          f"{v} {[m for _, m, _ in tab.tickets]}")
    # Ticket timeout NOT aborted -> terminal (double-buy guard).
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [{"raise": asyncio.TimeoutError(), "abort_read": {"s": "po", "aborted": False}},
                         t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_timeout_po_terminal", v == "terminal" and r.get("ambiguous_commit") is True
          and len(tab.tickets) == 1 and not ex.deletes, f"{v} {r}")
    # An unexpected exception with a place-order unresolved -> terminal, no clear.
    c = Clock()
    ex = bare(c)

    async def _tick(tab_, tcin, qty, h, mode, L):
        ex._woncart_po_unresolved = True
        raise KeyError("parser blew up")

    ex._api_checkout_ticket = _tick
    (v, r), _ = loop_run(ex, TicketTab(ex, [], c), c, FL_PRE429)
    check("e_exception_unresolved_terminal", v == "terminal" and r.get("ambiguous_commit") is True
          and not ex.deletes and ex._woncart_active_until == 0.0, f"{v} {r}")
    c = Clock()
    ex = bare(c)

    async def _tick2(tab_, tcin, qty, h, mode, L):
        raise KeyError("parser blew up")

    ex._api_checkout_ticket = _tick2
    (v, r), _ = loop_run(ex, TicketTab(ex, [], c), c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("e_exception_resolved_holds", v == "done" and r.get("won_cart_exit") == "loop_error"
          and r.get("reason") == "won_cart_held", r)
    # CancelledError propagates; quiet mode still cleared.
    c = Clock()
    ex = bare(c)

    async def _tick3(tab_, tcin, qty, h, mode, L):
        raise asyncio.CancelledError()

    ex._api_checkout_ticket = _tick3
    cancelled = False
    try:
        loop_run(ex, TicketTab(ex, [], c), c, FL_PRE429)
    except (asyncio.CancelledError, BaseException):
        cancelled = True
    check("e_cancel_propagates", cancelled and ex._woncart_active_until == 0.0)


def _bpm_classifier():
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    m = re.search(r"_transient_reasons = (\{.*?\n\s*\})", src, re.S)
    body = "\n".join(l.split("#", 1)[0] for l in m.group(1).splitlines())
    transient = set(ast.literal_eval(body))
    check("k_src_busy_add", "_transient_reasons.add('checkout_busy_retryable')" in src)
    check("k_src_qty_add", "if _qty_guard_on():\n                        _transient_reasons.add('cart_qty_cleared')" in src)
    transient |= {"checkout_busy_retryable", "cart_qty_cleared"}
    tm = re.search(r"_terminal_tokens = (\(.*?\))", src)
    terminal = ast.literal_eval(tm.group(1))
    restart = ("1011", "keepalive ping timeout", "connection closed", "websocket")

    def classify(res):
        reason = str(res.get("reason", "")).lower()
        err = str(res.get("error", "")).lower()
        if any(t in reason or t in err for t in terminal):
            return "terminal"
        if any(s in err for s in restart) or "cdp_wedged" in reason:
            return "restart"
        return "transient" if reason in transient else "final"

    return classify, terminal


def test_c_reason_classification():
    classify, terminal = _bpm_classifier()
    check("k_terminal_tokens_mirrored", tuple(terminal) == pe_mod._WONCART_BPM_TERMINAL_TOKENS, terminal)
    expected = {
        "cart_evicted": "transient", "qty_over": "transient", "cart_ticket_cap": "final",
        "tail_spent": "transient", "budget_spent": "transient", "call_cap": "transient",
        "yield_fleet": "transient", "probe_unknown": "transient", "foreign_stuck": "transient",
        "pre_unreadable": "transient", "pre_401_streak": "transient", "po_401_streak": "transient",
        "po_500": "transient", "unexpected_ticket_js_error": "transient", "loop_error": "transient",
    }
    for held in (False, True):
        for reason, want in expected.items():
            c = Clock()
            ex = bare(c)
            st = {"reason": reason, "dl": c.t + 200}
            L = {"tcin": TCIN, "tickets": 1}
            with in_tmp_cwd(), fake_time(c):
                res = run(ex._woncart_exit(TicketTab(ex, [], c), TCIN, L, st, c.t, held))
            w = want
            if held and reason not in ("cart_evicted", "qty_over", "cart_ticket_cap"):
                w = "final"            # won_cart_held: the level re-arm re-dispatches
            with env(TARGET_WONCART_DIRECT="1"):
                got = classify(res)
            check(f"k_exit[{reason},held={held}]", got == w, f"{res} -> {got}")
    # Stuck qty is final; terminal loop results are final (never re-raced by the thread).
    check("k_qty_stuck_final", classify({"reason": "cart_qty_stuck", "error": "won-cart loop: stacked line not deleted"}) == "final")
    check("k_terminal_final", classify({"reason": "checkout_navigation_failed",
                                        "error": "won-cart place-order unresolved (loop error)"}) == "final")
    with env(TARGET_FASTLANE_QTY_GUARD=None, TARGET_WONCART_DIRECT=None, TARGET_HELD_CART_REENTRY=None):
        check("k_qty_guard_off", bpm_mod._qty_guard_on() is False)
    with env(TARGET_WONCART_DIRECT="1"):
        check("k_qty_guard_forced", bpm_mod._qty_guard_on() is True)
    for r in ("oos_x", "sold_out", "reservation_failure", "unavailable_now", "out_of_stock"):
        safe = pe_mod.woncart_reason_safe(r)
        check(f"k_reason_safe[{r}]", not any(t in safe for t in terminal), safe)


def test_c_quiet_mode():
    c = Clock()
    ex = bare(c)
    ex._woncart_active_until = c.t + 100
    # Warmup refresh with force_fresh: no /cart nav; 401 heartbeat -> repair deferred.

    class WTab:
        def __init__(self):
            self.gets = []

        async def get(self, url):
            self.gets.append(url)

        async def evaluate(self, js, await_promise=False):
            ex._cached_cart_headers_ts = c.t + 1
            return 401

    wt = WTab()
    ex.session_manager.browser = object()
    ex._warmup_pool_lock = asyncio.Lock() if False else None
    ex._warmup_tab_cart_ts = {}
    ex._warmup_tabs = [wt]
    ex._last_bg_token_repair_ts = 0.0
    ex._atc_dead_token_midwindow_repair = True
    ex._bg_token_repair_times = []
    ex._churn_alerted_ts = 0.0
    confirms = []

    async def _confirm(*a):
        confirms.append(a)
        return 401

    async def _ens(idx):
        return wt

    ex._confirm_write_auth_401 = _confirm
    ex._ensure_warmup_tab = _ens

    async def go(force):
        ex._warmup_pool_lock = REAL_ASYNCIO.Lock()
        return await ex._refresh_on_tab(0, force_fresh=force)

    with fake_time(c):
        run(go(True))
    check("q_warmup_no_cart_nav", wt.gets == [] and "won-cart held — no /cart nav" in run.last_out, wt.gets)
    check("q_repair_deferred", confirms == [] and "repair deferred (won-cart loop)" in run.last_out,
          run.last_out[-300:])
    # Held cart marker alone also skips the nav.
    ex._woncart_active_until = 0.0
    ex._held_cart = {"tcin": TCIN}
    with fake_time(c):
        run(go(True))
    check("q_held_marker_no_cart_nav", wt.gets == [])
    # Not quiet -> force_fresh navigates (unchanged).
    ex._held_cart = None
    ex._last_bg_token_repair_ts = c.t          # inside the 300 s throttle -> no repair
    with fake_time(c):
        run(go(True))
    check("q_not_quiet_force_fresh_navigates", wt.gets == ["https://www.target.com/cart"], wt.gets)
    # Harvest: quiet -> no _harvest_once, no rotate nav.
    ex._woncart_active_until = c.t + 1000
    ex._harvest_cfg = {"bank": 3, "ttl_s": 60.0, "interval_s": 5.0, "selftest": False,
                       "in_window": True, "tcins": [TCIN]}
    ex._harvest_replay_on = True
    ex._harvest_disabled_reason = ""
    ex._harvest_prev_live = True
    ex._harvest_win = {"shots": 0, "replayed": 0, "a0": 0, "stale": 0}
    ex._harvest_landed_suspect = True
    once = []

    async def _once():
        once.append(1)
        return True

    ex._harvest_once = _once

    class Bank:
        def summary(self):
            return "bank"

        def need(self):
            return 3

        def refill_wanted(self):
            return True

    ex._shape_bank = Bank()
    ex._harvest_last_click_ts = 0.0
    susp = []

    async def _susp():
        susp.append(1)

    ex._harvest_clear_suspect_cart = _susp
    with fake_time(c, stop_after=6):
        run(ex._harvest_loop())
    check("q_harvest_tick_skipped", once == [] and susp == [], f"once={once} susp={susp}")
    del ex._harvest_once
    ht = WTab()
    ex._harvest_tab = ht
    ex._harvest_tcin_idx = 0
    with fake_time(c):
        r1 = run(PurchaseExecutor._harvest_once(ex))
        run(ex._harvest_rotate(same=False))
    check("q_harvest_once_and_rotate_quiet", r1 is False and ht.gets == [], ht.gets)
    # Quiet flag is time-bound.
    ex._woncart_active_until = c.t - 1
    with fake_time(c):
        expired = ex._woncart_quiet()
    ex._woncart_active_until = c.t + 1
    with fake_time(c):
        active = ex._woncart_quiet()
    check("q_quiet_expires", expired is False and active is True)


def test_c_helpers():
    c = Clock()
    ex = bare(c)
    ex._cached_cart_headers = {"X-a": "1", "Cookie": "x", "referer": "y"}
    ex._cached_cart_headers_ts = real_time.time()
    h = json.loads(ex._ticket_headers_js())
    check("h_headers_strip", h == {"X-a": "1", "x-application-name": "web"}, h)
    ex._cached_cart_headers_ts = real_time.time() - 120
    check("h_headers_90s_rule", json.loads(ex._ticket_headers_js()) == {"x-application-name": "web"})
    # Eligibility (pure).
    E = pe_mod.woncart_eligible
    check("h_elig_A", E(FL_PRE429) is True)
    check("h_elig_A_needs_atc_2xx", E(dict(FL_PRE429, atc={"status": 401})) is False)
    check("h_elig_B_body", E(FL_PO_FS) is True)
    check("h_elig_B_header_only_when_status_matches",
          E(dict(FL_PO_FS, po={"status": 429, "body": "", "fired": True}), 429,
            "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION") is True
          and E(dict(FL_PO_FS, po={"status": 429, "body": "", "fired": True}), 424,
                "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION") is False)
    check("h_elig_po0_false", E(FL_PO_0) is False)
    check("h_elig_424_false", E(FL_PO_424, 424, "RESERVATION_FAILURE") is False)
    check("h_elig_429_rf_false", E(dict(FL_PO_FS, po={"status": 429, "body": "RESERVATION_FAILURE", "fired": True}),
                                   429, "RESERVATION_FAILURE") is False)
    check("h_elig_foreign_false", E(dict(FL_PRE429, skip="foreign_cart_item")) is False)
    check("h_elig_qty_over_false", E(FL_QTY_OVER) is False)
    check("h_elig_garbage_false", E(None) is False and E({}) is False)
    # Arm gate.
    with env(TARGET_WONCART_DIRECT="1", TARGET_AMBIGUOUS_COMMIT_LATCH="1"), in_tmp_cwd():
        check("h_armed", ex._woncart_armed() is True)
    with env(TARGET_WONCART_DIRECT="0", TARGET_AMBIGUOUS_COMMIT_LATCH="1"):
        check("h_flag_off_not_armed", ex._woncart_armed() is False and ex._woncart_refusal_logged is False)
    # cfg parsing.
    cfg = pe_mod.woncart_cfg({})
    check("h_cfg_defaults", cfg["schedule"] == [5.0, 15.0] and cfg["steady_s"] == 45.0
          and cfg["max_tickets"] == 14 and cfg["call_max_s"] == 120.0 and cfg["headroom_s"] == 45.0
          and cfg["yield_fleet"] is True and cfg["oos_tail"] == 1 and cfg["pre_streak_max"] == 3, cfg)
    for raw in ("0", "none", "", " off "):
        check(f"h_cfg_no_probes[{raw!r}]", pe_mod.woncart_cfg({"TARGET_WONCART_SCHEDULE_S": raw})["schedule"] == [])
    cfg = pe_mod.woncart_cfg({"TARGET_WONCART_SCHEDULE_S": " 1 ,90, x, nan, 7, 8, 9",
                              "TARGET_WONCART_STEADY_GAP_S": "5", "TARGET_WONCART_MAX_TICKETS": "junk",
                              "TARGET_WONCART_YIELD_FLEET": " 0 ", "TARGET_WON_CART_RIDE": " 1 "})
    check("h_cfg_clamps", cfg["schedule"] == [3.0, 60.0, 7.0] and cfg["steady_s"] == 20.0
          and cfg["max_tickets"] == 14 and cfg["yield_fleet"] is False and cfg["ride_on"] is True, cfg)
    # _begin_won_cart_ride now returns the effective deadline (0.0 with no ctx).
    ex2 = bare(c)
    del ex2._begin_won_cart_ride
    ex2._purchase_timeout_ctx = None
    check("h_ride_returns_float", PurchaseExecutor._begin_won_cart_ride(ex2, c.t + 100) == 0.0
          and ex2._won_cart_ride_until > 0)
    # _stock_state never raises.
    ex3 = bare(c)
    ex3._stock_live_fn = lambda t: 1 / 0
    check("h_stock_state_safe", ex3._stock_state(TCIN) == {"live": None})
    ex3._stock_live_fn = None
    check("h_stock_state_nofn", ex3._stock_state(TCIN) == {"live": None})
    ex3._other_live_fn = lambda t: (_ for _ in ()).throw(RuntimeError())
    check("h_other_live_safe", ex3._other_live(TCIN) == [])


def test_c_cart_read_delete():
    c = Clock()
    ex = bare(c)
    del ex._delete_cart_items

    class CTab:
        def __init__(self, read, hang_delete=False):
            self.read = read
            self.hang_delete = hang_delete
            self.deleted = []

        async def evaluate(self, js, await_promise=False):
            if "method: 'DELETE'" in js:
                if self.hang_delete:
                    await REAL_ASYNCIO.sleep(30)
                self.deleted.append(re.search(r"cart_items/([A-Za-z0-9_-]+)'", js).group(1))
                return 204
            return self.read

    t = CTab({"ok": True, "status": 200, "items": [{"id": "A", "tcin": TCIN, "qty": 2},
                                                   {"id": "B", "tcin": OTHER, "qty": None},
                                                   {"id": "C", "tcin": OTHER, "qty": True}]})
    r = run(ex._cart_items_read(t))
    check("r_read_quantities", r["ok"] and [i["qty"] for i in r["items"]] == [2, None, None], r)
    ok, n = run(ex._delete_cart_items(t, only_tcin=TCIN))
    check("r_delete_only", ok and n == 1 and t.deleted == ["A"], t.deleted)
    t = CTab(t.read)
    ok, n = run(ex._delete_cart_items(t, keep_tcin=TCIN))
    check("r_delete_keep", ok and n == 2 and t.deleted == ["B", "C"], t.deleted)
    t = CTab({"ok": False, "status": 429, "items": []})
    check("r_delete_read_fail", run(ex._delete_cart_items(t)) == (False, 0) and t.deleted == [])
    t = CTab({"ok": True, "status": 200, "items": [{"id": "x'); evil(", "tcin": OTHER}]})
    check("r_delete_suspicious_id", run(ex._delete_cart_items(t)) == (False, 0) and t.deleted == [])
    t = CTab({"ok": True, "status": 200, "items": [{"id": "A", "tcin": TCIN}]}, hang_delete=True)
    t0 = real_time.time()
    res = run(ex._delete_cart_items(t, budget_s=1.0))
    check("r_delete_bounded", res == (False, 0) and real_time.time() - t0 < 3.0, f"{res} {real_time.time() - t0:.2f}")
    check("r_delete_no_budget", run(ex._delete_cart_items(t, budget_s=0.1)) == (False, 0))
    js = pe_mod._CART_ITEM_DELETE_JS.replace("@@CID@@", "A").replace("@@HEADERS@@", "{}")
    check("r_delete_js_no_token_repair", "token" not in js.lower() and "@@" not in js)
    check("r_node_available", bool(NODE), "node not on PATH — JS parse checks cannot run")
    if NODE:
        for name, src in (("read", pe_mod._CART_ITEMS_READ_JS), ("delete", js)):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8", dir=_TMP) as f:
                f.write(src)
            p = subprocess.run([NODE, "--check", f.name], capture_output=True, text=True, timeout=30)
            check(f"r_js_parses[{name}]", p.returncode == 0, p.stderr[:200])


# ───────────────────────── (d) manager stock probe ───────────────────────────

def mgr_with(statuses):
    m = object.__new__(BulletproofPurchaseManager)
    m.stock_monitor = types.SimpleNamespace(_resilient_checker=types.SimpleNamespace(_tcin_status=statuses))
    return m


def status(t, now, true_at=None, false_at=None, checked=None):
    s = scr_mod.TcinStatus(tcin=t)
    s.last_checked_at = now if checked is None else checked
    if true_at is not None:
        scr_mod.note_stock_read(s, True, true_at, 20.0)
    if false_at is not None:
        scr_mod.note_stock_read(s, False, false_at, 20.0)
    return s


def test_d_stock_snapshot():
    now = 1_800_000_000.0
    m = mgr_with({TCIN: status(TCIN, now, true_at=now)})
    check("s_fresh_true", m.stock_snapshot(TCIN, now)["live"] is True)
    m = mgr_with({TCIN: status(TCIN, now, true_at=now - 5, false_at=now)})
    snap = m.stock_snapshot(TCIN, now)
    check("s_false_within_hyst_true", snap["live"] is True and snap["window_start"] == now - 5, snap)
    m = mgr_with({TCIN: status(TCIN, now, true_at=now - 25, false_at=now)})
    check("s_false_after_hyst", m.stock_snapshot(TCIN, now)["live"] is False)
    m = mgr_with({TCIN: status(TCIN, now, false_at=now)})
    check("s_never_true_false", m.stock_snapshot(TCIN, now)["live"] is False)
    m = mgr_with({TCIN: status(TCIN, now, true_at=now - 1, checked=now - 16)})
    check("s_stale_none", m.stock_snapshot(TCIN, now)["live"] is None)
    m = mgr_with({TCIN: status(TCIN, now, true_at=now)})
    with env(TARGET_STOCK_PROBE="0"):
        check("s_killswitch_none", m.stock_snapshot(TCIN, now)["live"] is None
              and m.any_stock_live(None, now) == [])
    with env(TARGET_STOCK_HYST_S="30"):
        m2 = mgr_with({TCIN: status(TCIN, now, true_at=now - 25, false_at=now)})
        check("s_hyst_knob", m2.stock_snapshot(TCIN, now)["live"] is True)
    nm = object.__new__(BulletproofPurchaseManager)
    check("s_no_monitor_none", nm.stock_snapshot(TCIN, now)["live"] is None and nm.any_stock_live() == [])
    m = mgr_with({})
    check("s_unknown_tcin_none", m.stock_snapshot(TCIN, now)["live"] is None)
    m = mgr_with({TCIN: status(TCIN, now, true_at=now), OTHER: status(OTHER, now, true_at=now),
                  "999999": status("999999", now, false_at=now)})
    check("s_any_live_excludes", m.any_stock_live(TCIN, now) == [OTHER], m.any_stock_live(TCIN, now))

    class Flaky(dict):
        def __init__(self, *a, fails=2, **k):
            super().__init__(*a, **k)
            self.fails = fails

        def items(self):
            if self.fails > 0:
                self.fails -= 1
                raise RuntimeError("dictionary changed size during iteration")
            return super().items()

    m = mgr_with(Flaky({OTHER: status(OTHER, now, true_at=now)}, fails=2))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = m.any_stock_live(TCIN, now)
    check("s_any_live_two_failures_empty", r == [] and "[STOCK_PROBE]" in buf.getvalue(), r)
    m = mgr_with(Flaky({OTHER: status(OTHER, now, true_at=now)}, fails=1))
    check("s_any_live_one_retry_ok", m.any_stock_live(TCIN, now) == [OTHER])
    # Injection + submit stamp are in the real dispatch source.
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("s_injection_in_source", "target_purchase_executor._stock_live_fn = self.stock_snapshot" in src
          and "target_purchase_executor._other_live_fn = self.any_stock_live" in src)
    i_stamp = src.find("target_purchase_executor._mgr_submit_ts = time.time()")
    i_submit = src.find("target_purchase_executor.execute_purchase(tcin, quantity=qty)")
    check("s_submit_stamp_before_submit", 0 < i_stamp < i_submit)


# ───────────────────────── (e) SCR hysteresis ────────────────────────────────

def test_e_note_stock_read():
    s = scr_mod.TcinStatus(tcin=TCIN)
    check("n_defaults", s.last_true_at == 0.0 and s.window_start_at == 0.0 and s.pickup == "")
    scr_mod.note_stock_read(s, True, 100.0, 20.0)
    check("n_first_true_opens_window", s.window_start_at == 100.0 and s.last_true_at == 100.0)
    scr_mod.note_stock_read(s, False, 105.0, 20.0)
    scr_mod.note_stock_read(s, True, 115.0, 20.0)
    check("n_flicker_keeps_window", s.window_start_at == 100.0 and s.last_true_at == 115.0
          and s.last_false_at == 105.0)
    scr_mod.note_stock_read(s, True, 135.0, 20.0)
    check("n_gap_equal_hyst_keeps_window", s.window_start_at == 100.0)
    scr_mod.note_stock_read(s, True, 160.0, 20.0)
    check("n_gap_over_hyst_new_window", s.window_start_at == 160.0)
    scr_mod.note_stock_read(None, True, 1.0, 20.0)        # never raises
    check("n_in_stock_untouched", s.in_stock is False and s.last_checked_at == 0.0)
    with env(TARGET_STOCK_HYST_S="abc"):
        check("n_hyst_default", scr_mod.stock_hyst_s() == 20.0)
    with env(TARGET_STOCK_HYST_S="1"):
        check("n_hyst_clamp", scr_mod.stock_hyst_s() == 5.0)
    # Real sweep ingest wires it (True and False reads).
    chk = object.__new__(scr_mod.ResilientStockChecker)
    chk._status_lock = asyncio.Lock() if False else None
    chk._last_seen_at = {}
    chk._tcin_status = {}
    chk._ever_seen_in_stock = set()
    chk.on_in_stock = None
    chk._test_mode_loop = False
    parsed = {TCIN: {"in_stock": True, "availability_status": "IN_STOCK", "title": "x", "max_qty": 2},
              OTHER: {"in_stock": False, "availability_status": "OUT_OF_STOCK", "title": "y", "max_qty": 1}}
    chk._parse_bulk = lambda raw: parsed

    async def go():
        chk._status_lock = REAL_ASYNCIO.Lock()
        await chk._ingest_bulk_response(types.SimpleNamespace(raw={"data": 1}))

    asyncio.run(go())
    a, b = chk._tcin_status[TCIN], chk._tcin_status[OTHER]
    check("n_ingest_true", a.last_true_at > 0 and a.window_start_at == a.last_true_at
          and a.last_true_at == a.last_checked_at, vars(a))
    check("n_ingest_false", b.last_false_at > 0 and b.last_true_at == 0.0 and b.in_stock is False, vars(b))
    src = Path(scr_mod.__file__).read_text(encoding="utf-8")
    check("n_verify_and_ground_truth_wired",
          src.count("note_stock_read(s, True, s.last_checked_at, stock_hyst_s())") == 2)


# ─────────── (f) WC-3 held-cart re-entry + boot cart audit (stage S2c) ────────

HELD = dict(ARMED, TARGET_HELD_CART_REENTRY="1")
EXACT_READ = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2}]}
NEW_REASON_RESULTS: list = []


def mk_held(tcin=TCIN, age=10.0, **kw):
    now = real_time.time()
    h = {"tcin": tcin, "qty": 2, "cart_id": "CART-1234567890", "created": now - age,
         "first_201_ts": now - age, "tickets": 3, "sched_used": 2, "verified": True,
         "pi_id": "", "cvv_put": "none", "last_ticket_ts": now - 50.0, "fs_seen_ts": 0.0,
         "source": "first"}
    h.update(kw)
    return h


def held_impl(c, marker, read=None, probes=None, loop_result=None, **kw):
    ex, tab = impl_ex(c, FL_PRE429, loop_result=loop_result, **kw)
    ex._held_cart = marker
    ex.reads = []

    async def _read(t, timeout=2.5):
        ex.reads.append(timeout)
        return json.loads(json.dumps(read if read is not None else {"ok": True, "status": 200, "items": []}))

    ex._cart_items_read = _read
    probes = dict(probes or {})
    ex._stock_live_fn = lambda t: {"live": probes.get(str(t))}
    return ex, tab


def test_f_held_cart_reentry():
    c = Clock()
    # exact + live -> the loop (entry=held), no ATC chain; placed -> success dict.
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True}, loop_result=("placed", None))
    r = run_impl(ex, HELD)
    check("f_exact_live_placed", r.get("success") is True and r.get("order_id") == "OID-LOOP"
          and r.get("quantity") == 2 and ex.loop_calls == [(TCIN, 2, "held")] and ex.fl_calls == []
          and not ex.deletes and "checking_out" not in ex.statuses and ex.reads == [2.5]
          and "[HELD_CART] re-entering the won-cart loop" in run.last_out, f"{r} fl={ex.fl_calls} {ex.statuses}")
    check("f_exact_keeps_verified", ex._held_cart["verified"] is True)
    # A held line of qty 1 reports quantity 1 (read), not the requested 2.
    ex, tab = held_impl(c, mk_held(), read={"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 1}]},
                        probes={TCIN: True}, loop_result=("placed", None))
    r = run_impl(ex, HELD)
    check("f_placed_quantity_from_read", r.get("success") is True and r.get("quantity") == 1, r)
    # The loop's final non-success result is returned as-is (no fall-through).
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True})
    r = run_impl(ex, HELD)
    check("f_loop_result_returned", r.get("reason") == "won_cart_held" and ex.fl_calls == []
          and "checking_out" not in ex.statuses, r)
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True},
                        loop_result=("terminal", {"success": False, "tcin": TCIN,
                                                  "reason": "checkout_navigation_failed", "ambiguous_commit": True}))
    r = run_impl(ex, HELD)
    check("f_loop_terminal_returned", r.get("ambiguous_commit") is True and ex.fl_calls == [], r)
    # Not live (False / None) -> held_cart_idle_skip, nothing read, nothing fired.
    for live in (False, None):
        ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: live})
        r = run_impl(ex, HELD)
        NEW_REASON_RESULTS.append(r)
        check(f"f_idle_skip[{live}]", r.get("reason") == "held_cart_idle_skip" and r.get("success") is False
              and ex.reads == [] and ex.fl_calls == [] and ex.loop_calls == [] and not ex.deletes
              and isinstance(ex._held_cart, dict) and tab.gets == [], f"{r} {ex.reads} {tab.gets}")
    # Empty cart -> marker dropped, normal shot (fast lane called).
    ex, tab = held_impl(c, mk_held(), read={"ok": True, "status": 200, "items": []}, probes={TCIN: True})
    r = run_impl(ex, HELD)
    check("f_empty_normal_shot", ex._held_cart is None and ex.fl_calls == [2]
          and ex.loop_calls == [(TCIN, 2, "first")] and not ex.deletes, f"{r} {ex.loop_calls}")
    # Read 429 -> loop entered; the ticket's pre re-verifies (verified demoted).
    ex, tab = held_impl(c, mk_held(), read={"ok": False, "status": 429, "items": []}, probes={TCIN: True})
    r = run_impl(ex, HELD)
    check("f_read_429_enters_loop", ex.loop_calls == [(TCIN, 2, "held")] and ex.fl_calls == []
          and ex._held_cart["verified"] is False and "cart read failed" in run.last_out, r)
    # Unknown qty -> loop entered, verified demoted.
    ex, tab = held_impl(c, mk_held(), read={"ok": True, "status": 200,
                                            "items": [{"id": "CI-1", "tcin": TCIN, "qty": None}]},
                        probes={TCIN: True})
    run_impl(ex, HELD)
    check("f_unknown_qty_demotes", ex.loop_calls == [(TCIN, 2, "held")] and ex._held_cart["verified"] is False)
    # Foreign line beside the held one -> selective delete (keep T), loop entered.
    two = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2},
                                                {"id": "CI-F", "tcin": OTHER, "qty": 1}]}
    ex, tab = held_impl(c, mk_held(), read=two, probes={TCIN: True})
    run_impl(ex, HELD)
    check("f_foreign_selective", ex.deletes == [{"only": None, "keep": TCIN, "budget": 15.0}]
          and ex.loop_calls == [(TCIN, 2, "held")] and ex._held_cart["verified"] is True, ex.deletes)
    ex, tab = held_impl(c, mk_held(), read=two, probes={TCIN: True})
    ex.delete_result = (False, 0)
    run_impl(ex, HELD)
    check("f_foreign_delete_failed_demotes", ex.loop_calls == [(TCIN, 2, "held")]
          and ex._held_cart["verified"] is False)
    # Held line over qty -> delete ONLY that line, marker dropped, normal shot.
    ex, tab = held_impl(c, mk_held(), read={"ok": True, "status": 200,
                                            "items": [{"id": "CI-1", "tcin": TCIN, "qty": 4}]},
                        probes={TCIN: True})
    r = run_impl(ex, HELD)
    check("f_qty_over_released", ex.deletes == [{"only": TCIN, "keep": None, "budget": 15.0}]
          and ex._held_cart is None and ex.fl_calls == [2], f"{r} {ex.deletes}")
    ex, tab = held_impl(c, mk_held(), read={"ok": True, "status": 200,
                                            "items": [{"id": "CI-1", "tcin": TCIN, "qty": 4}]},
                        probes={TCIN: True})
    ex.delete_result = (False, 0)
    r = run_impl(ex, HELD)
    NEW_REASON_RESULTS.append(r)
    check("f_qty_over_delete_failed", r.get("reason") == "held_cart_release_failed" and ex.fl_calls == []
          and ex._held_cart is None, r)
    # Other TCIN held and LIVE -> sit out, cart untouched.
    ex, tab = held_impl(c, mk_held(tcin=OTHER), probes={OTHER: True, TCIN: True})
    r = run_impl(ex, HELD)
    NEW_REASON_RESULTS.append(r)
    check("f_other_live_sits_out", r.get("reason") == "held_cart_other_tcin" and not ex.deletes
          and ex.reads == [] and ex.fl_calls == [] and ex._held_cart["tcin"] == OTHER, r)
    # Other TCIN held and not live (False / None) -> bounded delete of ITS line, normal shot.
    for live in (False, None):
        ex, tab = held_impl(c, mk_held(tcin=OTHER), probes={OTHER: live, TCIN: True})
        r = run_impl(ex, HELD)
        check(f"f_other_released[{live}]", ex.deletes == [{"only": OTHER, "keep": None, "budget": 15.0}]
              and ex._held_cart is None and ex.fl_calls == [2] and "[HELD_CART] released" in run.last_out,
              f"{r} {ex.deletes}")
    # R3: the failed delete is confirmed by a read that still shows the line.
    ex, tab = held_impl(c, mk_held(tcin=OTHER), probes={OTHER: False},
                        read={"ok": True, "status": 200, "items": [{"id": "CI-9", "tcin": OTHER, "qty": 2}]})
    ex.delete_result = (False, 0)
    r = run_impl(ex, HELD)
    check("f_other_release_failed", r.get("reason") == "held_cart_release_failed" and ex.fl_calls == []
          and ex._held_cart is None, r)
    # TTL / ticket cap -> retire (bounded delete of the held line), then a normal shot.
    for label, marker in (("ttl", mk_held(age=1000.0)), ("cap", mk_held(tickets=14)),
                          ("no_created", mk_held(created=0.0))):
        ex, tab = held_impl(c, marker, read=EXACT_READ, probes={TCIN: True})
        r = run_impl(ex, HELD)
        check(f"f_retire[{label}]", ex.deletes == [{"only": TCIN, "keep": None, "budget": 15.0}]
              and ex._held_cart is None and ex.fl_calls == [2] and ex.reads == []
              and "[HELD_CART] retired" in run.last_out, f"{r} {ex.deletes}")
    with env(TARGET_HELD_CART_TTL_S="2000"):
        ex, tab = held_impl(c, mk_held(age=1000.0), read=EXACT_READ, probes={TCIN: True})
        run_impl(ex, HELD)
    check("f_ttl_knob", ex.loop_calls == [(TCIN, 2, "held")] and not ex.deletes)
    # A marker without a TCIN is dropped (nothing to delete) and the shot goes on.
    ex, tab = held_impl(c, mk_held(tcin=""), read=EXACT_READ, probes={TCIN: True})
    run_impl(ex, HELD)
    check("f_bad_marker_dropped", ex._held_cart is None and not ex.deletes and ex.fl_calls == [2])
    # API place-order off -> the loop cannot run: retire, then the (legacy) shot.
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True})
    run_impl(ex, dict(HELD, TARGET_API_PLACE_ORDER="false"))
    check("f_api_path_off_retires", ex.deletes and ex.deletes[0]["only"] == TCIN
          and ex._held_cart is None and ex.loop_calls == [] and ex.reads == [], ex.deletes)
    # CVV required without digits -> the loop cannot run: retire before any shot.
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True}, cvv_required=True)
    run_impl(ex, HELD)
    check("f_cvv_no_digits_retires", ex.deletes and ex.deletes[0]["only"] == TCIN
          and ex._held_cart is None and ex.loop_calls == [], ex.deletes)
    # WC-3 flag off -> the check block never runs (a marker cannot exist then anyway).
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True})
    run_impl(ex, ARMED)
    check("f_flag_off_no_check", ex.reads == [] and ex.fl_calls == [2]
          and ex.loop_calls == [(TCIN, 2, "first")] and isinstance(ex._held_cart, dict))
    # A check that errors fires nothing and keeps the cart + marker.
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True})
    ex._stock_live_fn = None
    ex._stock_state = lambda t: (_ for _ in ()).throw(RuntimeError("probe broke"))
    r = run_impl(ex, HELD)
    NEW_REASON_RESULTS.append(r)
    check("f_check_error_fires_nothing", r.get("reason") == "held_cart_error" and ex.fl_calls == []
          and not ex.deletes and isinstance(ex._held_cart, dict), r)
    # A 'terminal' first-entry loop never leaves a marker (real loop).
    c2 = Clock()
    ex = bare(c2)
    tab = TicketTab(ex, [t_prepo(0)], c2)
    (v, r), _ = loop_run(ex, tab, c2, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("f_terminal_never_sets_marker", v == "terminal" and ex._held_cart is None and not ex.deletes, f"{v} {r}")
    # End-to-end: real held check + real loop -> one po_only ticket -> order; no ATC.
    ex, _ = impl_ex(c, FL_PRE429)
    del ex._won_cart_ticket_loop
    ex._held_cart = mk_held()
    ex.probe = {"live": True}

    class E2ETab(TicketTab):
        handlers = {}
        enabled_domains = []

        async def get(self, url):
            raise AssertionError("no navigation expected: " + url)

        async def send(self, *a, **k):
            return None

    etab = E2ETab(ex, [t_po(200, ORDER_BODY)], c, cart_items=[{"id": "CI-1", "tcin": TCIN, "qty": 2}])

    async def _gp():
        return etab

    ex.session_manager.get_page = _gp
    r = run_impl(ex, HELD)
    check("f_e2e_held_po_only_order", r.get("success") is True and r.get("order_id") == "OID-777"
          and [m for _, m, _ in etab.tickets] == ["po_only"] and ex.fl_calls == []
          and ex._held_cart is None and etab.deleted == [] and "[WON_CART_DIRECT] start entry=held" in run.last_out,
          f"{r} {[m for _, m, _ in etab.tickets]} {run.last_out[-300:]}")


def test_f_held_protections():
    c = Clock()
    # _held_cart_active: TTL-aware; a marker without a stamp counts as active.
    ex = bare(c)
    check("p_active_none", ex._held_cart_active() is False)
    ex._held_cart = mk_held(age=10.0)
    check("p_active_fresh", ex._held_cart_active() is True)
    ex._held_cart = mk_held(age=1000.0)
    check("p_active_expired", ex._held_cart_active() is False)
    ex._held_cart = {"tcin": TCIN}
    check("p_active_unstamped", ex._held_cart_active() is True)
    check("p_ttl_clamps", pe_mod.held_cart_ttl_s({}) == 900.0
          and pe_mod.held_cart_ttl_s({"TARGET_HELD_CART_TTL_S": "5"}) == 60.0
          and pe_mod.held_cart_ttl_s({"TARGET_HELD_CART_TTL_S": "99999"}) == 3600.0
          and pe_mod.held_cart_ttl_s({"TARGET_HELD_CART_TTL_S": "junk"}) == 900.0
          and pe_mod.held_cart_ttl_s({"TARGET_HELD_CART_TTL_S": "nan"}) == 900.0)
    with env(TARGET_HELD_CART_REENTRY=None, TARGET_BOOT_CART_AUDIT=None):
        check("p_boot_audit_off_by_default", pe_mod.boot_cart_audit_on() is False)
    with env(TARGET_HELD_CART_REENTRY="1", TARGET_BOOT_CART_AUDIT=None):
        check("p_boot_audit_auto_on", pe_mod.boot_cart_audit_on() is True)
    with env(TARGET_HELD_CART_REENTRY="1", TARGET_BOOT_CART_AUDIT="0"):
        check("p_boot_audit_killswitch", pe_mod.boot_cart_audit_on() is False)

    # Harvest suspect-clear keeps the held line (selective delete), else the full clear.
    def suspect_ex(marker):
        e = bare(c)
        e._harvest_tab = object()
        e._harvest_landed_suspect = True
        e.drops, e.clears = [], []

        async def _drop(why):
            e.drops.append(why)

        async def _clr(t):
            e.clears.append(t)
            return True

        e._harvest_drop_tab = _drop
        e._clear_cart = _clr
        e._held_cart = marker
        return e

    e = suspect_ex(mk_held())
    with env(**HELD):
        run(e._harvest_clear_suspect_cart())
    check("p_suspect_keeps_held", e.deletes == [{"only": None, "keep": TCIN, "budget": 15.0}]
          and e.clears == [] and len(e.drops) == 1 and e._harvest_landed_suspect is False, f"{e.deletes} {e.clears}")
    e = suspect_ex(mk_held())
    with env(TARGET_HELD_CART_REENTRY=None):
        run(e._harvest_clear_suspect_cart())
    check("p_suspect_flag_off_full_clear", e.deletes == [] and len(e.clears) == 1 and len(e.drops) == 1)
    e = suspect_ex(None)
    with env(**HELD):
        run(e._harvest_clear_suspect_cart())
    check("p_suspect_no_marker_full_clear", e.deletes == [] and len(e.clears) == 1)

    # Error-recovery clear drops the marker (flag on, clear succeeded only).
    for label, flags, cleared, want_none in (("on", HELD, True, True), ("off", ARMED, True, False),
                                             ("clear_failed", HELD, False, False)):
        ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True})

        async def _boom(*a, **k):
            raise RuntimeError("unexpected")

        ex._held_cart_entry = _boom
        ex._api_fast_lane = _boom
        rtab = ImplTab()
        ex.session_manager.browser = types.SimpleNamespace(tabs=[rtab])

        async def _clr(t, _v=cleared):
            return _v

        ex._clear_cart = _clr
        r = run_impl(ex, flags)
        check(f"p_recovery_clear_marker[{label}]", r.get("reason") == "exception"
              and (ex._held_cart is None) == want_none, f"{r} {ex._held_cart}")


def boot_ex(c, read, busy=False):
    ex = bare(c)
    ex.session_manager.live = busy
    ex.ensured = []
    wt = types.SimpleNamespace(name="warmup0")

    async def _ens(idx):
        ex.ensured.append(idx)
        return wt

    ex._ensure_warmup_tab = _ens
    ex.reads = []

    async def _read(t, timeout=2.5):
        ex.reads.append((t, timeout, ex._warmup_pool_lock.locked()))
        if callable(read):
            return read()
        return json.loads(json.dumps(read))

    ex._cart_items_read = _read
    return ex, wt


def boot_run(ex, c, flags=None, delay_s=0.0):
    async def go():
        ex._warmup_pool_lock = REAL_ASYNCIO.Lock()
        return await ex._boot_cart_audit(delay_s=delay_s)

    e = dict(IMPL_ENV)
    e.update(HELD if flags is None else flags)
    with in_tmp_cwd(), env(**e), fake_time(c) as shim:
        out = run(go())
    return out, shim


def test_f_boot_audit():
    c = Clock()
    one = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2}]}
    ex, wt = boot_ex(c, one)
    out, _ = boot_run(ex, c)
    h = ex._held_cart
    check("b_single_line_held", out == "held" and isinstance(h, dict) and h["tcin"] == TCIN and h["qty"] == 2
          and h["source"] == "boot" and h["verified"] is False and h["tickets"] == 0
          and h["sched_used"] == 0 and h["created"] == c.t and not ex.deletes
          and ex.ensured == [0] and ex.reads == [(wt, 5.0, True)]
          and "[BOOT_CART_AUDIT] single line" in run.last_out, f"{out} {h} {ex.reads}")
    cases = [
        ("over_ceiling", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 3}]}),
        ("two_lines", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 1},
                                                             {"id": "CI-2", "tcin": OTHER, "qty": 1}]}),
        ("same_tcin_two_lines", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 1},
                                                                       {"id": "CI-2", "tcin": TCIN, "qty": 1}]}),
        ("unknown_qty", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": None}]}),
        ("zero_qty", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 0}]}),
        ("bad_tcin", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": "", "qty": 1}]}),
    ]
    for label, read in cases:
        ex, wt = boot_ex(c, read)
        out, _ = boot_run(ex, c)
        # R1 review: only the ids this read saw, with a busy re-check per DELETE.
        d0 = ex.deletes[0] if len(ex.deletes) == 1 else {}
        check(f"b_not_clean_deleted[{label}]", out == "deleted" and ex._held_cart is None
              and {k: d0.get(k) for k in ("only", "keep", "budget")} == {"only": None, "keep": None, "budget": 15.0}
              and d0.get("ids") == [i["id"] for i in read["items"]]
              and callable(d0.get("abort")) and d0["abort"]() is False, f"{out} {ex.deletes}")
    with env(TARGET_QTY_CEILING="3"):
        ex, wt = boot_ex(c, cases[0][1])
        out, _ = boot_run(ex, c)
    check("b_ceiling_knob", out == "held" and ex._held_cart["qty"] == 3)
    ex, wt = boot_ex(c, cases[1][1])
    ex.delete_result = (False, 0)
    out, _ = boot_run(ex, c)
    check("b_delete_failed", out == "delete_failed" and ex._held_cart is None)
    ex, wt = boot_ex(c, {"ok": True, "status": 200, "items": []})
    out, _ = boot_run(ex, c)
    check("b_empty", out == "empty" and not ex.deletes and ex._held_cart is None)
    ex, wt = boot_ex(c, {"ok": False, "status": 429, "items": []})
    out, _ = boot_run(ex, c)
    check("b_read_failed_no_action", out == "read_failed" and not ex.deletes and ex._held_cart is None)
    # A purchase owns the cart -> wait (15 s x 10, fake clock), then give up; nothing read.
    ex, wt = boot_ex(c, one, busy=True)
    out, shim = boot_run(ex, c)
    check("b_busy_gives_up", out == "busy" and ex.reads == [] and shim.sleeps == [0.0] + [15.0] * 10
          and ex._held_cart is None and not ex.deletes, f"{out} {shim.sleeps}")
    # Busy once, then free -> proceeds.
    ex, wt = boot_ex(c, one)
    ex.session_manager.live = True
    states = iter([True, False, False, False])
    ex.session_manager.is_purchase_in_progress = lambda: next(states, False)
    out, shim = boot_run(ex, c)
    check("b_busy_then_free", out == "held" and shim.sleeps == [0.0, 15.0], f"{out} {shim.sleeps}")
    # A purchase starts during the read -> no action.
    ex, wt = boot_ex(c, None)

    def _flip():
        ex.session_manager.live = True
        return json.loads(json.dumps(cases[1][1]))

    ex, wt = boot_ex(c, _flip)
    out, _ = boot_run(ex, c)
    check("b_busy_during_read", out == "busy" and not ex.deletes and ex._held_cart is None, out)
    # The page lock held by a purchase also counts as busy.
    ex, wt = boot_ex(c, one)
    lock_state = types.SimpleNamespace(locked=lambda: True)
    ex._page_lock = lock_state
    out, _ = boot_run(ex, c)
    check("b_page_lock_busy", out == "busy" and ex.reads == [])
    # A marker that already exists is never overwritten.
    ex, wt = boot_ex(c, one)
    ex._held_cart = mk_held(tcin=OTHER)
    out, _ = boot_run(ex, c)
    check("b_marker_exists", out == "marker_exists" and ex.reads == [] and ex._held_cart["tcin"] == OTHER)
    # Not armed (WC-3 on, WC-1 off) -> skipped; no warmup tab -> no action.
    ex, wt = boot_ex(c, one)
    out, _ = boot_run(ex, c, flags={"TARGET_HELD_CART_REENTRY": "1"})
    check("b_not_armed", out == "not_armed" and ex.reads == [])
    ex, wt = boot_ex(c, one)
    out, _ = boot_run(ex, c, flags=dict(HELD, TARGET_API_PLACE_ORDER="false"))
    check("b_api_path_off", out == "not_armed" and ex.reads == [])
    ex, wt = boot_ex(c, one)

    async def _no_tab(idx):
        return None

    ex._ensure_warmup_tab = _no_tab
    out, _ = boot_run(ex, c)
    check("b_no_tab", out == "no_tab" and ex.reads == [])
    # Default delay is 30-60 s; an exception is swallowed.
    ex, wt = boot_ex(c, lambda: 1 / 0)
    out, shim = boot_run(ex, c, delay_s=None)
    check("b_default_delay_and_error", out == "error" and 30.0 <= shim.sleeps[0] <= 60.0, f"{out} {shim.sleeps}")
    # Spawn: flag off -> nothing; on -> exactly one task (via _start_background_refill); test_mode -> none.
    for label, flags, test_mode, want in (("off", {"TARGET_HELD_CART_REENTRY": None}, False, 0),
                                          ("on", HELD, False, 1), ("test_mode", HELD, True, 0),
                                          ("killswitch", dict(HELD, TARGET_BOOT_CART_AUDIT="0"), False, 0)):
        ex = bare(c)
        ex.test_mode = test_mode
        ex._start_harvest = lambda: None
        ex._warmup_refill_tasks = []
        ex._warmup_pool_size = 0
        spawned = []

        async def _audit(delay_s=None, _s=spawned):
            _s.append(delay_s)
            return "stub"

        ex._boot_cart_audit = _audit

        async def go(_e=ex):
            _e._start_background_refill()
            _e._start_background_refill()
            t = getattr(_e, "_boot_cart_audit_task", None)
            return (await t) if t is not None else None

        with env(**flags):
            res = run(go())
        check(f"b_spawn[{label}]", len(spawned) == want and (res == "stub") == (want == 1), f"{spawned} {res}")


def test_f_new_reason_classification():
    classify, terminal = _bpm_classifier()
    # won_cart_ride_timeout from the real hang branch (clean exit on, ride live).
    c = Clock()
    ex = bare(c)
    ex._purchase_timeout_ctx = None
    ex._note_atc_gate_outcome = lambda t, r: None

    async def _impl(tcin, quantity=1, _e=ex):
        _e._won_cart_ride_until = real_time.time() + 100
        await REAL_ASYNCIO.sleep(5)

    ex._execute_purchase_impl = _impl
    shim = types.SimpleNamespace(**{n: getattr(REAL_ASYNCIO, n) for n in dir(REAL_ASYNCIO) if not n.startswith("__")})
    shim.timeout = lambda s: REAL_ASYNCIO.timeout(0.05)
    saved = pe_mod.asyncio
    pe_mod.asyncio = shim
    try:
        async def go():
            ex._page_lock = REAL_ASYNCIO.Lock()
            return await ex.execute_purchase(TCIN, quantity=2)
        with env(TARGET_WON_CART_RIDE_CLEAN_EXIT="1"):
            r = run(go())
    finally:
        pe_mod.asyncio = saved
    check("k2_ride_timeout_shape", r.get("reason") == "won_cart_ride_timeout" and "websocket" not in str(r).lower()
          and r.get("ambiguous_commit") is False, r)
    NEW_REASON_RESULTS.append(r)
    seen = set()
    for res in NEW_REASON_RESULTS:
        seen.add(res.get("reason"))
        with env(**HELD):
            got = classify(res)
        check(f"k2_final[{res.get('reason')}]", got == "final", f"{res} -> {got}")
        check(f"k2_no_terminal_token[{res.get('reason')}]",
              not any(t in str(res.get("reason", "")).lower() for t in terminal))
        # The manager restarts the browser on '1011' anywhere in a failed
        # result's error; hot TCINs start with 1011 -> no digit runs at all.
        check(f"k2_error_digit_free[{res.get('reason')}]",
              not re.search(r"\d{3,}", str(res.get("error", ""))), res.get("error"))
    check("k2_all_new_reasons_seen", {"held_cart_idle_skip", "held_cart_other_tcin", "held_cart_release_failed",
                                      "held_cart_error", "won_cart_ride_timeout"} <= seen, seen)


def test_g_wc2_impl():
    c = Clock()
    # 5c: the ride deadline is trimmed to now+20 s when the purchase returns.
    for flag, ride_rel, want in (("1", 500.0, "trimmed"), ("0", 500.0, "kept"), ("1", None, "zero")):
        ex, tab = impl_ex(c, FL_PRE429)

        async def _loop(t, tcin, qty, fl, start_time, entry="first", _e=ex, _r=ride_rel):
            if _r is not None:
                _e._won_cart_ride_until = real_time.time() + _r
            return "done", {"success": False, "tcin": tcin, "reason": "won_cart_held"}

        ex._won_cart_ticket_loop = _loop
        run_impl(ex, dict(ARMED, TARGET_WON_CART_RIDE_CLEAN_EXIT=flag))
        left = ex._won_cart_ride_until - real_time.time()
        if want == "trimmed":
            ok = 0 < left <= 20.5
        elif want == "kept":
            ok = left > 400
        else:
            ok = ex._won_cart_ride_until == 0.0
        check(f"g_ride_trim[{flag},{want}]", ok, f"left={left:.1f}")

    # Item 4: the legacy fire-and-forget pre_checkout is skipped when the fast
    # lane already awaited one (flag on), fired otherwise.
    class DupTab(ImplTab):
        def __init__(self):
            super().__init__()
            self.evals = []

        async def get(self, url):
            self.gets.append(url)
            if "checkout/start" in url:
                raise LegacyReached("checkout nav")

        async def evaluate(self, js, await_promise=False):
            self.evals.append(js)
            return None

    def pre_fires(fl0, flags):
        ex, _ = impl_ex(c, fl0)
        dt = DupTab()

        async def _gp():
            return dt

        ex.session_manager.get_page = _gp
        ex._notify_status = lambda *a, **k: None
        run_impl(ex, flags)
        return sum(1 for j in dt.evals if "pre_checkout?cart_type=REGULAR" in j and "keepalive: true" in j), dt

    n, dt = pre_fires(FL_PRE429, {})
    check("g_dup_pre_default_fires", n == 1 and any("checkout/start" in g for g in dt.gets), n)
    n, dt = pre_fires(FL_PRE429, {"TARGET_FASTLANE_SKIP_DUP_PRE": "1"})
    check("g_dup_pre_skipped", n == 0 and any("checkout/start" in g for g in dt.gets)
          and "pre_checkout NOT re-fired" in run.last_out, n)
    n, dt = pre_fires(dict(FL_PRE429, pre={"status": 0}, skip="pre_0"), {"TARGET_FASTLANE_SKIP_DUP_PRE": "1"})
    check("g_dup_pre_fires_when_fast_lane_had_no_pre", n == 1, n)


# ─────── (h) stage S5, plan P6 (FL-1): timeout outcomes at the call site ───────

def test_h_fl1_call_site():
    """The dicts FL-1's timeout branch synthesizes (built by the real pure
    fast_lane_timeout_outcome) through the real _execute_purchase_impl."""
    classify, _ = _bpm_classifier()
    c = Clock()
    fl_atc = pe_mod.fast_lane_timeout_outcome({"s": "atc", "aborted": True, "atc": 0})
    fl_pre0 = pe_mod.fast_lane_timeout_outcome({"s": "pre", "aborted": True, "atc": 201})
    fl_cvv0 = pe_mod.fast_lane_timeout_outcome({"s": "cvv", "aborted": True, "atc": 201})
    # Aborted during the ATC -> the transient legacy reason, no error text, no
    # loop, no legacy checkout, no "chain stopped" line — armed or not.
    for label, e in (("armed", ARMED), ("off", {})):
        ex, tab = impl_ex(c, fl_atc)
        r = run_impl(ex, e)
        check(f"fl1_site_atc_timeout_transient[{label}]",
              r.get("reason") == "atc_evaluate_timeout" and r.get("success") is False
              and "error" not in r and not r.get("ambiguous_commit")
              and not ex.loop_calls and "checking_out" not in ex.statuses
              and classify(r) == "transient" and ex.fl_calls == [2]
              and "chain stopped before place-order" not in run.last_out,
              f"{r} loop={ex.loop_calls} st={ex.statuses}")
        check(f"fl1_site_atc_timeout_no_cart_writes[{label}]", not ex.deletes and not ex._po_ambiguous)
    # Aborted at pre / cvv after a 2xx ATC -> won-cart loop (first entry) when
    # armed; today's pre_0 legacy fallthrough when not.
    for label, fl0 in (("pre", fl_pre0), ("cvv", fl_cvv0)):
        ex, tab = impl_ex(c, fl0)
        r = run_impl(ex, ARMED)
        check(f"fl1_site_{label}_abort_enters_loop", ex.loop_calls == [(TCIN, 2, "first")]
              and r.get("reason") == "won_cart_held" and "checking_out" not in ex.statuses,
              f"{r} {ex.loop_calls} {ex.statuses}")
        ex, tab = impl_ex(c, fl0)
        run_impl(ex, {})
        check(f"fl1_site_{label}_abort_legacy_when_off", not ex.loop_calls and "checking_out" in ex.statuses,
              f"{ex.loop_calls} {ex.statuses}")
    # The loop seeds an UNVERIFIED ledger from it (first ticket = pre_po).
    ex = bare(c)
    L = ex._woncart_new_ledger(TCIN, 2, fl_pre0, c.t)
    check("fl1_site_pre0_ledger_unverified", L["verified"] is False and L["fs_seen_ts"] == 0.0
          and L["cart_id"] == "" and L["pi_id"] == "", L)
    # The not-aborted outcome (None) keeps today's terminal no-response dict.
    check("fl1_site_po_stage_not_synthesized",
          pe_mod.fast_lane_timeout_outcome({"s": "po", "aborted": False, "atc": 201}) is None)


# ───────────────────── (r1) review round R1 fixes (2026-09-17) ─────────────────────

def _held_rerun(ex, tab, c, gap_s, **flags):
    """One held re-entry `gap_s` after the previous call (fresh purchase)."""
    c.t += gap_s
    ex._execute_started_at = c.t
    ex._mgr_submit_ts = c.t
    ex.ride_return = c.t + 290
    return loop_run(ex, tab, c, None, entry="held", TARGET_HELD_CART_REENTRY="1", **flags)


def test_r1_yield_fleet_never_locks_out_held_reentry():
    """R1-ARM-1: TARGET_WONCART_YIELD_FLEET=1 + TARGET_HELD_CART_REENTRY=1 used to
    yield every held re-entry before its first ticket (the schedule is spent),
    while held_cart_other_tcin kept the identity off every other live TCIN."""
    c = Clock()
    ex = bare(c)
    ex._other_live_fn = lambda t: [OTHER]
    tab = TicketTab(ex, [t_pre(429)] * 20, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("r1y_first_entry_still_yields_after_probes", len(tab.tickets) == 2
          and r.get("won_cart_exit") == "yield_fleet" and ex._held_cart is not None, f"{len(tab.tickets)} {r}")
    counts = []
    for _ in range(3):
        before = len(tab.tickets)
        (v, r), _ = _held_rerun(ex, tab, c, 70.0)
        counts.append((len(tab.tickets) - before, r.get("won_cart_exit"), r.get("reason")))
    check("r1y_each_held_reentry_fires_one_steady_then_yields",
          counts == [(1, "yield_fleet", "won_cart_held")] * 3 and ex._held_cart is not None
          and ex._held_cart["tickets"] == 5 and all(m == "pre_po" for _, m, _ in tab.tickets), counts)
    # Re-entered before the steady slot is due: waits for it, fires, then yields.
    before = len(tab.tickets)
    t_call = c.t + 10.0
    (v, r), shim = _held_rerun(ex, tab, c, 10.0)
    check("r1y_held_reentry_waits_for_due_slot", len(tab.tickets) - before == 1
          and r.get("won_cart_exit") == "yield_fleet" and len(shim.sleeps) == 1
          and 20.0 <= shim.sleeps[0] <= 40.0 and tab.tickets[-1][0] > t_call, f"{shim.sleeps} {r}")
    # Empty schedule (kill-switch): the first entry fires its steady ticket too.
    c = Clock()
    ex = bare(c)
    ex._other_live_fn = lambda t: [OTHER]
    tab = TicketTab(ex, [t_pre(429)] * 5, c)
    with env(TARGET_WONCART_SCHEDULE_S="0"):
        (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("r1y_no_schedule_first_entry_fires", len(tab.tickets) == 1
          and r.get("won_cart_exit") == "yield_fleet", f"{len(tab.tickets)} {r}")
    # No other TCIN live: the held call keeps its 45 s cadence up to the call cap.
    c = Clock()
    ex = bare(c)
    ex._other_live_fn = lambda t: []
    tab = TicketTab(ex, [t_pre(429)] * 20, c)
    loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    before = len(tab.tickets)
    (v, r), _ = _held_rerun(ex, tab, c, 70.0)
    check("r1y_no_other_live_no_yield", len(tab.tickets) - before >= 2
          and r.get("won_cart_exit") in ("call_cap", "budget_spent"), f"{len(tab.tickets) - before} {r}")


class _FakeTimeoutCtx:
    def __init__(self):
        self.whens = []

    def reschedule(self, when):
        self.whens.append(when)


def test_r1_ride_kept_through_success_tail():
    """R1-DB-1: a placed / unresolved loop keeps the ride (and the executor
    timeout 10 s inside it) until _execute_purchase_impl's cleanup is done, so
    a stall after the order ends in the executor's own hang branch (success),
    never in the manager's execution_timeout."""
    # Real _begin_won_cart_ride + _extend_purchase_timeout on the fake clock.
    c = Clock()
    t0 = c.t
    ex = bare(c)
    del ex._begin_won_cart_ride                  # the real method
    ex._purchase_timeout_ctx = _FakeTimeoutCtx()
    tab = TicketTab(ex, [t_prepo(429, FS_BODY), t_po(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    ru = ex._won_cart_ride_until
    check("r1d_placed_keeps_ride", v == "placed" and abs(ru - (t0 + 300.0)) < 1e-6
          and ex._woncart_trim_at_exit is True, f"{v} ride_in={ru - c.t:.1f}")
    check("r1d_exec_timeout_inside_ride", len(ex._purchase_timeout_ctx.whens) == 1, ex._purchase_timeout_ctx.whens)
    # The manager at its 150 s wall (and again at +285 s) keeps waiting, so the
    # executor's own timeout (t0+290) fires first.
    allow = BulletproofPurchaseManager._ride_extension_allowed
    check("r1d_manager_keeps_waiting", allow(ru, t0 + 150.0, 150.0, 300.0, True)
          and allow(ru, t0 + 289.0, 289.0, 300.0, True), ru - t0)
    # Unresolved place-order: same, and the hang branch would still latch.
    c = Clock()
    t0 = c.t
    ex = bare(c)
    tab = TicketTab(ex, [t_po(0)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS)
    check("r1d_terminal_keeps_ride", v == "terminal" and ex._won_cart_ride_until == t0 + 1000.0
          and ex._woncart_trim_at_exit is True and ex._po_ambiguous is True, f"{v} {ex._won_cart_ride_until - t0}")
    # A non-placed, non-held exit still trims (unchanged).
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_skip("cart_empty")], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("r1d_done_exit_still_trims", v == "done" and ex._won_cart_ride_until <= c.t + 20.0 + 1e-6
          and not getattr(ex, "_woncart_trim_at_exit", False), ex._won_cart_ride_until - c.t)
    # The impl's finally trims after the cleanup when the loop handed it over,
    # even with TARGET_WON_CART_RIDE_CLEAN_EXIT off; a fresh purchase resets it.
    for label, loop_v, want_trim in (("placed", "placed", True), ("terminal", "terminal", True),
                                     ("held", "done", False)):
        ex, tab = impl_ex(c, FL_PRE429)

        async def _loop(t, tcin, qty, fl, start_time, entry="first", _e=ex, _v=loop_v):
            _e._won_cart_ride_until = real_time.time() + 500.0
            if _v == "placed":
                _e._fastlane_placed = True
                _e._api_order_id = "OID-R1"
            if _v in ("placed", "terminal"):
                _e._woncart_trim_at_exit = True
                return _v, (None if _v == "placed" else {"success": False, "tcin": tcin,
                                                         "reason": "checkout_navigation_failed",
                                                         "ambiguous_commit": True})
            return "done", {"success": False, "tcin": tcin, "reason": "won_cart_held"}

        ex._won_cart_ticket_loop = _loop
        res = run_impl(ex, dict(ARMED, TARGET_WON_CART_RIDE_CLEAN_EXIT="0"))
        left = ex._won_cart_ride_until - real_time.time()
        ok = (0 < left <= 20.5) if want_trim else (left > 400)
        check(f"r1d_impl_exit_trim[{label}]", ok and (res.get("success") is (label == "placed")),
              f"left={left:.1f} {res}")
        # The next purchase starts with the hand-over flag cleared.

        async def _held_loop(t, tcin, qty, fl, start_time, entry="first"):
            return "done", {"success": False, "tcin": tcin, "reason": "won_cart_held"}

        ex._won_cart_ticket_loop = _held_loop
        run_impl(ex, dict(ARMED))
        check(f"r1d_trim_flag_reset[{label}]", ex._woncart_trim_at_exit is False)
    src = Path(pe_mod.__file__).read_text(encoding="utf-8")
    j = src.find("if ride_clean_exit_on() or getattr(self, '_woncart_trim_at_exit', False):")
    i = src.rfind("await tab.send(cdp.fetch.disable())", 0, j)
    check("r1d_trim_after_cleanup", j > 0 and 0 < j - i < 2500, (i, j))


def test_r1_cvv_ledger_seed():
    """R1-CVV-1 / R1-WONCART-CVV-DOUBLE-PUT / PC-2: <= 1 CVV PUT per cart,
    counting the fast-lane chain's own PUT and a PUT that may be on the wire."""
    seed = pe_mod.woncart_cvv_seed
    for fl, want in (({"cvv": {"put": 200, "first": True}}, "ok"),
                     ({"cvv": {"put": 204}}, "ok"),
                     ({"cvv": {"put": 400, "first": True}}, "failed"),
                     ({"cvv": {"put": 0}}, "failed"),
                     ({"cvv": {"put": -1, "reshot": True}}, "ok"),
                     ({"cvv": {"put": -1, "first": False}}, "none"),
                     ({"cvv": {"put": -2, "first": True}}, "none"),
                     ({"cvv": {"put": True}}, "none"),
                     ({"skip": "pre_0", "stage": "cvv"}, "unknown"),
                     ({"skip": "pre_0", "stage": "pre"}, "none"),
                     ({}, "none"), (None, "none"), ({"cvv": "junk"}, "none")):
        check(f"r1c_seed[{json.dumps(fl, sort_keys=True)}]", seed(fl) == want, seed(fl))
    c = Clock()
    ex = bare(c)
    fl_b = dict(FL_PO_FS, cvv={"put": 200, "first": True, "reshot": False})
    L = ex._woncart_new_ledger(TCIN, 2, fl_b, c.t, 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION")
    check("r1c_ledger_b_seeded", L["cvv_put"] == "ok" and L["verified"] is True, L)
    # Latched account, entry (B) after a pre-PUT: the first ticket sends no PUT.
    ex = bare(c, cvv="123", cvv_required=True)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    tab = TicketTab(ex, [t_po(429, FS_BODY)] * 3, c)
    loop_run(ex, tab, c, fl_b, TARGET_WONCART_MAX_TICKETS="1")
    js0 = tab.tickets[0][2]
    check("r1c_latched_b_no_second_put", "const CVV_FIRST = false;" in js0
          and "const CVV_REACTIVE = false;" in js0 and tab.tickets[0][1] == "po_only", js0[:0])
    # Unlatched account, (B) after the in-lane 3b PUT + re-shoot: no reactive PUT.
    ex = bare(c, cvv="123", cvv_required=False)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    fl_3b = dict(FL_PO_FS, cvv={"put": 200, "first": False, "reshot": True, "po1": 400})
    tab = TicketTab(ex, [t_po(429, FS_BODY)] * 3, c)
    loop_run(ex, tab, c, fl_3b, TARGET_WONCART_MAX_TICKETS="1")
    js0 = tab.tickets[0][2]
    check("r1c_3b_no_reactive_put", "const CVV_REACTIVE = false;" in js0 and "const CVV_FIRST = false;" in js0)
    # No PUT yet on the cart (unchanged): the latched account still pre-PUTs once.
    ex = bare(c, cvv="123", cvv_required=True)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    tab = TicketTab(ex, [t_po(429, FS_BODY)] * 3, c)
    loop_run(ex, tab, c, dict(FL_PO_FS, cvv={"put": -1, "first": False}), TARGET_WONCART_MAX_TICKETS="1")
    check("r1c_no_prior_put_unchanged", "const CVV_FIRST = true;" in tab.tickets[0][2])
    # FL-1 synthesized pre_0 aborted at stage 'cvv': no PUT from the first ticket.
    ex = bare(c, cvv="123", cvv_required=True)
    syn = pe_mod.fast_lane_timeout_outcome({"s": "cvv", "aborted": True, "atc": 201})
    tab = TicketTab(ex, [t_pre(429)] * 3, c)
    loop_run(ex, tab, c, syn, TARGET_WONCART_MAX_TICKETS="1")
    js0 = tab.tickets[0][2]
    check("r1c_fl1_cvv_stage_no_put", "const CVV_FIRST = false;" in js0 and tab.tickets[0][1] == "pre_po")
    # A ticket aborted at stage 'cvv' may have sent its PUT: the next one does not.
    ex = bare(c, cvv="123", cvv_required=True)
    tab = TicketTab(ex, [{"raise": REAL_ASYNCIO.TimeoutError(), "abort_read": {"s": "cvv", "aborted": True}},
                         t_pre(429), t_pre(429)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_WONCART_MAX_TICKETS="2")
    check("r1c_ticket_cvv_abort_marks_unknown",
          "const CVV_FIRST = true;" in tab.tickets[0][2] and "const CVV_FIRST = false;" in tab.tickets[1][2]
          and "const CVV_REACTIVE = false;" in tab.tickets[1][2], [m for _, m, _ in tab.tickets])
    # ...but an abort before the PUT stage (pre) leaves the one PUT available.
    ex = bare(c, cvv="123", cvv_required=True)
    tab = TicketTab(ex, [{"raise": REAL_ASYNCIO.TimeoutError(), "abort_read": {"s": "pre", "aborted": True}},
                         t_pre(429), t_pre(429)], c)
    loop_run(ex, tab, c, FL_PRE429, TARGET_WONCART_MAX_TICKETS="2")
    check("r1c_ticket_pre_abort_keeps_put", "const CVV_FIRST = true;" in tab.tickets[1][2])


def test_r1_po_only_suspect_fallback():
    """R1-QTY-1: a po_only ticket buys the cart unread; it falls back to pre_po
    while one of our own adds may have landed after the cart was verified."""
    c = Clock()
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    ex._harvest_landed_suspect = True
    ex._harvest_landed_suspect_ts = c.t          # flagged as the loop starts
    tab = TicketTab(ex, [t_prepo(429, FS_BODY), t_po(429, FS_BODY), t_po(429, FS_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="3")
    modes = [m for _, m, _ in tab.tickets]
    # R3 (R2-SUSPECT-READ-TOCTOU): inside the 300 s late-add window a pre_po
    # gate no longer clears the suspicion; the (empty) cart read keeps every
    # ticket on the strict gate.
    check("r1q_harvest_suspect_forces_pre_po_in_window", modes == ["pre_po", "pre_po", "pre_po"]
          and "late add of ours (harvest_add)" in run.last_out, modes)
    # A new harvest suspicion mid-loop re-arms the fallback once.
    c = Clock()
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    steps = [t_po(429, FS_BODY), t_prepo(429, FS_BODY), t_po(429, FS_BODY)]

    class _FlagTab(TicketTab):
        flagged = False

        async def evaluate(self, js, await_promise=False, **kw):
            res = await super().evaluate(js, await_promise, **kw)
            # One-shot (R2: the suspect cart READ is another evaluate at the
            # same fake time and must not re-raise the flag).
            if len(self.tickets) == 1 and not self.flagged:
                self.flagged = True
                self.ex._harvest_landed_suspect = True
                self.ex._harvest_landed_suspect_ts = self.clock.t
            return res

    tab = _FlagTab(ex, steps, c)
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="3")
    modes = [m for _, m, _ in tab.tickets]
    check("r1q_mid_loop_suspect", modes == ["po_only", "pre_po", "pre_po"], modes)
    # An orphaned ATC (FL-1 atc abort / legacy 8 s timeout) within 300 s: pre_po only.
    for age, want in ((10.0, ["pre_po", "pre_po"]), (301.0, ["po_only", "po_only"])):
        c = Clock()
        ex = bare(c)
        ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        ex._orphan_atc_ts = c.t - age
        tab = TicketTab(ex, [t_prepo(429, FS_BODY), t_prepo(429, FS_BODY)] if want[0] == "pre_po"
                        else [t_po(429, FS_BODY)] * 2, c)
        loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="2")
        modes = [m for _, m, _ in tab.tickets]
        check(f"r1q_orphan_atc[{age:.0f}s]", modes == want, modes)
    # The strict gate then refuses a stacked cart (qty 4 of Q 2).
    c = Clock()
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    ex._orphan_atc_ts = c.t
    tab = TicketTab(ex, [t_skip("cart_qty_over", qty=4)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS)
    check("r1q_orphan_stack_never_bought", [m for _, m, _ in tab.tickets] == ["pre_po"]
          and r.get("reason") == "cart_qty_cleared" and ex.deletes and ex.deletes[-1]["only"] == TCIN, r)
    # Stamps.
    src = Path(pe_mod.__file__).read_text(encoding="utf-8")
    check("r1q_legacy_atc_timeout_stamps",
          "ATC fetch evaluate timed out after 8s — CDP wedged, aborting purchase\")\n"
          "                    self._orphan_atc_ts = time.time()" in src.replace("\r\n", "\n"))
    check("r1q_harvest_suspect_stamps", "self._harvest_landed_suspect = True\n"
          "            self._harvest_landed_suspect_ts = time.time()" in src.replace("\r\n", "\n"))


class ReadTab(TicketTab):
    """TicketTab whose cart READ answer is scripted: `reads` is a list of
    read results (dict = returned as is; Exception = raised); the last one
    repeats. Records the fake-clock time of every read."""

    def __init__(self, ex, script, clock, reads):
        super().__init__(ex, script, clock)
        self.reads = list(reads)
        self.read_ts = []

    async def evaluate(self, js, await_promise=False, **kw):
        if "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            self.read_ts.append(self.clock.t)
            r = self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]
            if isinstance(r, BaseException):
                raise r
            return json.loads(json.dumps(r))
        return await super().evaluate(js, await_promise, **kw)


R2_EXACT = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2}]}


def _r2_suspect_ex(c, orphan_age=10.0):
    ex = bare(c)
    ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    ex._orphan_atc_ts = c.t - orphan_age
    ex._woncart_suspect_cleared_ts = 0.0
    return ex


def test_r2_po_only_suspect_read():
    """R2-QTY1-PO-SHAPE: while an orphaned add of ours may land, a verified
    cart keeps its po_only tickets (the 08-04 shape) when a cart READ shows
    exactly our TCIN at qty 1..Q; anything else goes through the strict gate."""
    # Orphan suspect + exact read -> po_only, a read before EVERY ticket, and a
    # place-order goes out even while pre_checkout would answer FAST_SELLING.
    c = Clock()
    ex = _r2_suspect_ex(c)
    tab = ReadTab(ex, [t_po(429, FS_BODY)] * 3, c, [R2_EXACT])
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="3")
    modes = [m for _, m, _ in tab.tickets]
    check("r2q_exact_read_keeps_po_only", modes == ["po_only"] * 3 and len(tab.read_ts) == 3
          and "po_only kept" in run.last_out and "pre_po instead" not in run.last_out, (modes, run.last_out[-400:]))
    check("r2q_read_right_before_each_ticket",
          [round(t, 3) for t in tab.read_ts] == [round(t, 3) for t, _, _ in tab.tickets], (tab.read_ts, tab.tickets))
    check("r2q_exact_read_three_place_orders", r.get("reason") == "won_cart_retired"
          and r.get("won_cart_exit") == "cart_ticket_cap"
          and all("const MODE = 'po_only'" in js for _, _, js in tab.tickets), r)
    # A 1-unit line (qty below Q) is exact too.
    c = Clock()
    ex = _r2_suspect_ex(c)
    one = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 1}]}
    tab = ReadTab(ex, [t_po(429, FS_BODY)], c, [one])
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="1")
    check("r2q_qty1_exact", [m for _, m, _ in tab.tickets] == ["po_only"], tab.tickets)
    # Anything but exact -> pre_po (strict gate), with the read described.
    bad_reads = (
        ("stacked", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 4}]},
         "1 line(s) 1010892069x4"),
        ("two_lines_over", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2},
                                                                 {"id": "CI-2", "tcin": TCIN, "qty": 2}]}, "2 line(s)"),
        ("foreign", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2},
                                                          {"id": "CI-9", "tcin": OTHER, "qty": 1}]}, "2 line(s)"),
        ("empty", {"ok": True, "status": 200, "items": []}, "0 line(s)"),
        ("qty_unknown", {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": None}]},
         "1010892069xNone"),
        ("read_429", {"ok": False, "status": 429, "items": []}, "failed status=429"),
        ("read_raises", RuntimeError("websocket closed"), "failed status=0 err=RuntimeError"),
        ("read_hangs", REAL_ASYNCIO.TimeoutError(), "failed status=0 err=TimeoutError"),
    )
    for label, rd, desc in bad_reads:
        c = Clock()
        ex = _r2_suspect_ex(c)
        tab = ReadTab(ex, [t_skip("cart_qty_over", qty=4)] if label == "stacked" else [t_pre(429, FS_BODY)],
                      c, [rd])
        (v, r), _ = loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="1")
        check(f"r2q_not_exact_pre_po[{label}]", [m for _, m, _ in tab.tickets] == ["pre_po"]
              and len(tab.read_ts) == 1 and "pre_po instead of po_only (cart read: " in run.last_out
              and desc in run.last_out, (tab.tickets, run.last_out[-300:]))
        if label == "stacked":
            check("r2q_stacked_never_bought", r.get("reason") == "cart_qty_cleared"
                  and ex.deletes and ex.deletes[-1]["only"] == TCIN, r)
    # Kill-switch: TARGET_WONCART_SUSPECT_READ=0 -> R1 behaviour (no read, pre_po).
    c = Clock()
    ex = _r2_suspect_ex(c)
    tab = ReadTab(ex, [t_pre(429, FS_BODY)], c, [R2_EXACT])
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="1", TARGET_WONCART_SUSPECT_READ="0")
    check("r2q_killswitch_r1_behaviour", [m for _, m, _ in tab.tickets] == ["pre_po"] and not tab.read_ts
          and "(cart read: read off)" in run.last_out, (tab.tickets, tab.read_ts))
    # No suspicion -> no read at all (the verified po_only path is unchanged).
    c = Clock()
    ex = _r2_suspect_ex(c, orphan_age=301.0)
    tab = ReadTab(ex, [t_po(429, FS_BODY)] * 2, c, [R2_EXACT])
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="2")
    check("r2q_no_suspect_no_read", [m for _, m, _ in tab.tickets] == ["po_only"] * 2 and not tab.read_ts,
          (tab.tickets, tab.read_ts))
    # R3 (R2-SUSPECT-READ-TOCTOU): a fresh harvest suspicion is re-read before
    # EVERY po_only ticket (the add may land after any one read)...
    c = Clock()
    ex = _r2_suspect_ex(c, orphan_age=301.0)
    ex._harvest_landed_suspect = True
    ex._harvest_landed_suspect_ts = c.t
    tab = ReadTab(ex, [t_po(429, FS_BODY)] * 3, c, [R2_EXACT])
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="3")
    check("r2q_harvest_suspect_read_every_ticket", [m for _, m, _ in tab.tickets] == ["po_only"] * 3
          and len(tab.read_ts) == 3, (tab.tickets, tab.read_ts, ex._woncart_suspect_cleared_ts))
    # ...and one read after the late-add window clears it: then plain po_only.
    c = Clock()
    ex = _r2_suspect_ex(c, orphan_age=301.0)
    ex._harvest_landed_suspect = True
    ex._harvest_landed_suspect_ts = c.t - 400.0
    tab = ReadTab(ex, [t_po(429, FS_BODY)] * 3, c, [R2_EXACT])
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="3")
    check("r2q_harvest_suspect_one_read_after_window", [m for _, m, _ in tab.tickets] == ["po_only"] * 3
          and len(tab.read_ts) == 1 and ex._woncart_suspect_cleared_ts == tab.read_ts[0],
          (tab.tickets, tab.read_ts, ex._woncart_suspect_cleared_ts))
    # ...a failed read leaves the harvest suspicion to the strict gate (every ticket in the window).
    c = Clock()
    ex = _r2_suspect_ex(c, orphan_age=301.0)
    ex._harvest_landed_suspect = True
    ex._harvest_landed_suspect_ts = c.t
    tab = ReadTab(ex, [t_prepo(429, FS_BODY), t_po(429, FS_BODY)], c, [{"ok": False, "status": 503, "items": []}])
    loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="2")
    check("r2q_harvest_failed_read_pre_po_in_window", [m for _, m, _ in tab.tickets] == ["pre_po", "pre_po"]
          and len(tab.read_ts) == 2, (tab.tickets, tab.read_ts))
    # Pure helper.
    ex_ = pe_mod.woncart_read_exact
    check("r2q_exact_unit", ex_(R2_EXACT, TCIN, 2) == 2 and ex_(R2_EXACT, int(TCIN), 2) == 2
          and ex_({"ok": True, "items": [{"tcin": TCIN, "qty": 1.0}]}, TCIN, 2) == 1)
    for label, rd, q in (("over_q", R2_EXACT, 1), ("ok_truthy_not_true", dict(R2_EXACT, ok=1), 2),
                         ("zero", {"ok": True, "items": [{"tcin": TCIN, "qty": 0}]}, 2),
                         ("fraction", {"ok": True, "items": [{"tcin": TCIN, "qty": 1.5}]}, 2),
                         ("nan", {"ok": True, "items": [{"tcin": TCIN, "qty": float("nan")}]}, 2),
                         ("inf", {"ok": True, "items": [{"tcin": TCIN, "qty": float("inf")}]}, 2),
                         ("bool", {"ok": True, "items": [{"tcin": TCIN, "qty": True}]}, 2),
                         ("str_qty", {"ok": True, "items": [{"tcin": TCIN, "qty": "2"}]}, 2),
                         ("bad_item", {"ok": True, "items": ["x"]}, 2),
                         ("items_not_list", {"ok": True, "items": "x"}, 2),
                         ("not_dict", None, 2), ("blank_tcin", {"ok": True, "items": [{"tcin": "", "qty": 1}]}, 2)):
        check(f"r2q_exact_unit_none[{label}]", ex_(rd, TCIN, q) is None, ex_(rd, TCIN, q))
    check("r2q_desc_never_raises", isinstance(pe_mod.woncart_read_desc({"ok": True, "items": [None, 3]}), str)
          and pe_mod.woncart_read_desc(None) == "no result")
    check("r2q_cfg_default_on", pe_mod.woncart_cfg({})["suspect_read"] is True
          and pe_mod.woncart_cfg({"TARGET_WONCART_SUSPECT_READ": " 0 "})["suspect_read"] is False)


def test_r2_held_entry_tagged():
    """R2-DX-HELD-PASS: every final result of a held-cart re-entry (no ATC
    fired) is tagged woncart_entry='held' and is not a DX-1 shot; a normal shot
    after the marker is dropped is untagged and still counts."""
    from src.purchasing import identity_rest as ir
    c = Clock()
    cases = (
        ("loop_held", dict(read=EXACT_READ, probes={TCIN: True}), None, "won_cart_held"),
        ("placed", dict(read=EXACT_READ, probes={TCIN: True}, loop_result=("placed", None)), None, None),
        ("terminal", dict(read=EXACT_READ, probes={TCIN: True},
                          loop_result=("terminal", {"success": False, "tcin": TCIN, "ambiguous_commit": True,
                                                    "reason": "checkout_navigation_failed"})),
         None, "checkout_navigation_failed"),
        ("retired_loop", dict(read=EXACT_READ, probes={TCIN: True},
                              loop_result=("done", {"success": False, "tcin": TCIN,
                                                    "reason": "won_cart_retired"})), None, "won_cart_retired"),
        ("idle_skip", dict(read=EXACT_READ, probes={TCIN: False}), None, "held_cart_idle_skip"),
        ("other_live", dict(probes={OTHER: True, TCIN: True}), OTHER, "held_cart_other_tcin"),
    )
    for label, kw, htcin, reason in cases:
        ex, tab = held_impl(c, mk_held(tcin=htcin or TCIN), **kw)
        r = run_impl(ex, HELD)
        check(f"r2h_tagged[{label}]", r.get("woncart_entry") == "held" and ex.fl_calls == []
              and (r.get("success") is True if reason is None else r.get("reason") == reason), r)
        check(f"r2h_not_a_shot[{label}]", ir.classify_result(r) is None, ir.classify_result(r))
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True},
                        loop_result=("terminal", {"success": False, "tcin": TCIN, "ambiguous_commit": True,
                                                  "reason": "checkout_navigation_failed"}))
    r = run_impl(ex, HELD)
    check("r2h_terminal_keeps_ambiguous", r.get("ambiguous_commit") is True, r)
    # Marker dropped (empty cart) -> a normal shot: untagged, a recorded pass.
    ex, tab = held_impl(c, mk_held(), read={"ok": True, "status": 200, "items": []}, probes={TCIN: True})
    r = run_impl(ex, HELD)
    check("r2h_normal_shot_untagged", "woncart_entry" not in r and ex.fl_calls == [2]
          and ir.classify_result(r) == "pass", r)
    # Retired marker (TTL) -> released, then a normal shot: untagged.
    ex, tab = held_impl(c, mk_held(age=10_000.0), read=EXACT_READ, probes={TCIN: True})
    r = run_impl(ex, HELD)
    check("r2h_retired_then_shot_untagged", "woncart_entry" not in r and ex.fl_calls == [2], r)
    # Re-entry flag off: no marker path, untagged.
    ex, tab = held_impl(c, mk_held(), read=EXACT_READ, probes={TCIN: True})
    r = run_impl(ex, ARMED)
    check("r2h_flag_off_untagged", "woncart_entry" not in r, r)


def test_r2_fs_ticket_ms_since_201():
    """R2-DX-201TS: [FS_TICKET] ms_since_201 is measured from the browser's ATC
    2xx stamp (out.atc.t1), not from loop entry; '-' when no such stamp exists
    (FL-1 pre_0 entry, stamps off, stale/garbage stamp) and for a boot marker."""
    def _ms(out):
        return [re.search(r"ms_since_201=(\S+)", l).group(1)
                for l in out.splitlines() if l.startswith("[FS_TICKET]")]

    def _run(fl0, label):
        c = Clock()
        ex = bare(c)
        tab = TicketTab(ex, [t_pre(429, FS_BODY)] * 2, c)
        fl = json.loads(json.dumps(fl0)) if fl0 is not None else None
        if isinstance(fl, dict) and isinstance(fl.get("atc"), dict) and "t1_age" in fl["atc"]:
            age = fl["atc"].pop("t1_age")
            fl["atc"]["t1"] = age if isinstance(age, (str, bool)) else int((c.t - age) * 1000)
        (v, r), _ = loop_run(ex, tab, c, fl, TARGET_WONCART_MAX_TICKETS="2", TARGET_FS_TICKET_LOG="1")
        return _ms(run.last_out), ex

    fl_ok = dict(FL_PRE429, atc=dict(FL_PRE429["atc"], t0=0, t1_age=1.5))
    ms, ex = _run(fl_ok, "t1")
    # Chain came back 1.5 s after the 201; tickets at +5 s and +20 s of entry.
    check("r2t_seeded_from_atc_t1", ms == ["6500", "21500"], ms)
    L = ex._woncart_new_ledger(TCIN, 2, dict(FL_PRE429, atc={"status": 201, "t1": 1_799_999_998_500}),
                               1_800_000_000.0)
    check("r2t_ledger_src", L["first_201_src"] == "atc_t1" and L["first_201_ts"] == 1_799_999_998.5, L)
    for label, age in (("stale_61s", 61.0), ("future", -2.0), ("bool", True), ("string", "123")):
        ms, _ = _run(dict(FL_PRE429, atc=dict(FL_PRE429["atc"], t1_age=age)), label)
        check(f"r2t_unmeasured_dash[{label}]", ms == ["-", "-"], ms)
    ms, _ = _run(FL_PRE429, "no_stamp")
    check("r2t_no_stamp_dash", ms == ["-", "-"], ms)
    syn = pe_mod.fast_lane_timeout_outcome({"s": "pre", "aborted": True, "atc": 201})
    ms, _ = _run(syn, "fl1_pre0")
    check("r2t_fl1_pre0_dash", ms == ["-", "-"], ms)
    # A boot marker (no 201 ever seen) re-entered as held -> '-'.
    c = Clock()
    ex = bare(c)
    ex._held_cart = mk_held(source="boot", first_201_src="boot", verified=False, last_ticket_ts=0.0,
                            tickets=0)
    tab = TicketTab(ex, [t_pre(429, FS_BODY)], c)
    loop_run(ex, tab, c, None, entry="held", TARGET_HELD_CART_REENTRY="1",
             TARGET_WONCART_MAX_TICKETS="1", TARGET_FS_TICKET_LOG="1")
    check("r2t_boot_marker_dash", _ms(run.last_out) == ["-"], run.last_out[-400:])
    # The held re-entry of a stamped cart keeps measuring from that 201.
    c = Clock()
    ex = bare(c)
    ex._held_cart = mk_held(first_201_src="atc_t1", first_201_ts=c.t - 100.0, last_ticket_ts=c.t - 50.0,
                            tickets=3)
    tab = TicketTab(ex, [t_po(429, FS_BODY)], c)
    loop_run(ex, tab, c, None, entry="held", TARGET_HELD_CART_REENTRY="1",
             TARGET_WONCART_MAX_TICKETS="4", TARGET_FS_TICKET_LOG="1")
    got = _ms(run.last_out)
    check("r2t_held_keeps_201", len(got) == 1 and got[0].isdigit() and int(got[0]) >= 100_000, got)
    # The boot audit's marker is labelled.
    src = Path(pe_mod.__file__).read_text(encoding="utf-8")
    check("r2t_boot_marker_src", "'first_201_src': 'boot', 'tickets': 0," in src)


class _QuickWaitShim(AsyncioShim):
    """AsyncioShim whose wait_for budgets are scaled x0.005 (12 s -> 60 ms,
    8 s -> 40 ms, 2 s -> 10 ms); sleep() still advances the fake clock."""

    async def wait_for(self, aw, timeout=None):
        return await REAL_ASYNCIO.wait_for(aw, None if timeout is None else timeout * 0.005)


@contextlib.contextmanager
def quick_fake_time(clock):
    shim = _QuickWaitShim(clock)
    saved = (pe_mod.time, pe_mod.asyncio)
    pe_mod.time = TimeShim(clock)
    pe_mod.asyncio = shim
    try:
        yield shim
    finally:
        pe_mod.time, pe_mod.asyncio = saved


class FlHangTab(ReadTab):
    """The fast-lane chain (or the legacy 8 s ATC evaluate) hangs; the FL-1
    read-and-abort answers `fl_abort`; tickets / cart reads as ReadTab."""

    def __init__(self, ex, script, clock, reads, fl_abort=None):
        super().__init__(ex, script, clock, reads)
        self.fl_abort = fl_abort
        self.hung = 0
        self.fl_reads = 0

    async def evaluate(self, js, await_promise=False, **kw):
        if "atc: e.atc" in js and "e.abort = true" in js:
            self.fl_reads += 1
            return self.fl_abort
        if "const MODE = '" not in js and "web_checkouts/v1/cart?" not in js and (
                "const _SK = '" in js or "cart_items" in js):
            self.hung += 1
            await REAL_ASYNCIO.sleep(30)
            return {}
        return await super().evaluate(js, await_promise, **kw)


def test_r2_fl1_orphan_stamp_feeds_loop():
    """R2-TEST-FL1-ORPHAN: the REAL FL-1 timeout branch (armed: stage tracking +
    WC-1 forcing the qty guard) stamps _orphan_atc_ts on an 'atc'-stage abort,
    and a won-cart loop entered right after reads the cart before any po_only
    ticket (pre_po when the read is not exact). Replacing the stamp with `pass`
    fails this test."""
    for label, rd, want in (("empty_read", {"ok": True, "status": 200, "items": []}, "pre_po"),
                            ("stacked_read", {"ok": True, "status": 200,
                                              "items": [{"id": "CI-1", "tcin": TCIN, "qty": 4}]}, "pre_po"),
                            ("exact_read", R2_EXACT, "po_only")):
        c = Clock()
        ex = bare(c)
        ex._orphan_atc_ts = 0.0
        ex._woncart_suspect_cleared_ts = 0.0
        tab = FlHangTab(ex, [t_skip("cart_qty_over", qty=4) if want == "pre_po" else t_po(429, FS_BODY)],
                        c, [rd], fl_abort={"s": "atc", "aborted": True, "atc": 0})
        e = dict(ARMED, TARGET_FASTLANE_STAGE_TRACK="1")
        with in_tmp_cwd(), env(**e), quick_fake_time(c):
            res = run(ex._api_fast_lane(tab, TCIN, 2, "{}"))
        check(f"r2o_real_fl1_atc_abort[{label}]", res.get("skip") == "evaluate_timeout_atc"
              and tab.hung == 1 and tab.fl_reads == 1, (res, tab.hung, tab.fl_reads))
        check(f"r2o_real_fl1_stamp[{label}]", ex._orphan_atc_ts == c.t, (ex._orphan_atc_ts, c.t))
        c.t += 20.0                                   # the re-race wins a cart 20 s later
        ex._execute_started_at = ex._mgr_submit_ts = c.t
        ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
        (v, r), _ = loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="1")
        modes = [m for _, m, _ in tab.tickets]
        check(f"r2o_loop_reads_before_po_only[{label}]", modes == [want] and len(tab.read_ts) == 1
              and "late add of ours (orphan_atc)" in run.last_out, (modes, tab.read_ts))
        if label == "stacked_read":
            check("r2o_stacked_orphan_never_bought", r.get("reason") == "cart_qty_cleared", r)
    # Legacy path (fast lane off): the 8 s ATC evaluate timeout stamps too.
    c = Clock()
    ex, _itab = impl_ex(c, FL_PRE429)
    ex._orphan_atc_ts = 0.0
    tab = FlHangTab(ex, [], c, [R2_EXACT])
    for name in ("url", "handlers", "enabled_domains", "gets"):
        setattr(tab, name, getattr(_itab, name))
    tab.get = _itab.get
    tab.send = _itab.send

    async def _get_page():
        return tab

    ex.session_manager.get_page = _get_page
    with in_tmp_cwd(), env(**dict(IMPL_ENV, TARGET_FAST_LANE="0")), quick_fake_time(c):
        r = run(ex._execute_purchase_impl(TCIN, quantity=2))
    check("r2o_legacy_atc_timeout_stamps", r.get("reason") == "atc_evaluate_timeout"
          and tab.hung == 1 and ex._orphan_atc_ts == c.t and not ex.fl_calls,
          (r, tab.hung, ex._orphan_atc_ts, c.t, run.last_out[-300:]))


GLOBAL_HARNESS = r"""
const K = '__0123456789ab';
Object.defineProperty(globalThis, K, {value: {other: {s: 'pre', abort: false}}, enumerable: false,
                                      configurable: true, writable: true});
globalThis.fetch = (url, opts) => Promise.resolve({status: 429, text: async () => '{}'});
(async () => {
  await (%s);
  const kept = Object.prototype.hasOwnProperty.call(globalThis, K);
  const entries = kept ? Object.getOwnPropertyNames(globalThis[K]) : null;
  const inOp = K in globalThis;
  delete globalThis[K].other;
  await (%s);
  const gone = !Object.getOwnPropertyNames(globalThis).includes(K) && !(K in globalThis);
  console.log(JSON.stringify({kept, entries, inOp, gone}));
})().catch(e => console.log(JSON.stringify({error: String(e)})));
"""


def test_r1_page_global_removed():
    """R1-PAGE-GLOBAL-PERSISTS: the stage container leaves the page with its
    last entry, and never while another run still has an entry in it."""
    check("r1g_node_available", bool(NODE), "node not on PATH")
    if not NODE:
        return
    js1 = ticket_js("po_only", n="t1")
    js2 = ticket_js("po_only", n="t2")
    src = GLOBAL_HARNESS % (js1, js2)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8", dir=_TMP) as f:
        f.write(src)
    p = subprocess.run([NODE, f.name], capture_output=True, text=True, timeout=30)
    res = json.loads((p.stdout.strip().splitlines() or ['{"error": "no output"}'])[-1])
    check("r1g_container_kept_while_other_entry", res.get("kept") is True and res.get("entries") == ["other"]
          and res.get("inOp") is True, res)
    check("r1g_container_removed_when_empty", res.get("gone") is True, res)


def test_r1_boot_audit_delete_race():
    """R1-BOOTAUDIT-DELETE-RACE: the boot audit deletes only the lines its own
    read saw, and stops the moment a purchase owns the cart."""
    c = Clock()
    ex = bare(c)
    del ex._delete_cart_items                  # the real bounded delete

    class RTab:
        def __init__(self, on_delete=None):
            self.deleted = []
            self.reads = 0
            self.on_delete = on_delete

        async def evaluate(self, js, await_promise=False):
            if "method: 'DELETE'" in js:
                self.deleted.append(re.search(r"cart_items/([A-Za-z0-9_-]+)'", js).group(1))
                if self.on_delete:
                    self.on_delete(self)
                return 204
            self.reads += 1
            return {"ok": True, "status": 200, "items": [{"id": "staleA", "tcin": OTHER, "qty": 4},
                                                         {"id": "WON201", "tcin": TCIN, "qty": 2}]}

    t = RTab()
    res = run(ex._delete_cart_items(t, ids=["staleA", "staleB"]))
    check("r1b_ids_no_second_read", res == (True, 2) and t.reads == 0 and t.deleted == ["staleA", "staleB"],
          f"{res} {t.deleted}")
    t = RTab()
    res = run(ex._delete_cart_items(t, ids=["ok-1", "bad id!"]))
    check("r1b_ids_validated", res == (False, 0) and t.deleted == [], f"{res} {t.deleted}")
    flips = {"busy": False}
    t = RTab(on_delete=lambda _t: flips.__setitem__("busy", True))
    res = run(ex._delete_cart_items(t, ids=["s1", "s2", "s3"], abort_fn=lambda: flips["busy"]))
    check("r1b_abort_between_deletes", res == (False, 1) and t.deleted == ["s1"], f"{res} {t.deleted}")
    t = RTab()
    res = run(ex._delete_cart_items(t, ids=["s1"], abort_fn=lambda: 1 / 0))
    check("r1b_abort_fn_error_stops", res == (False, 0) and t.deleted == [], res)
    # End to end: a purchase starts right after the audit's read.
    ex, wt = boot_ex(c, {"ok": True, "status": 200, "items": [{"id": "staleA", "tcin": OTHER, "qty": 4},
                                                              {"id": "staleB", "tcin": TCIN, "qty": 1}]})
    del ex._delete_cart_items
    tab = RTab(on_delete=lambda _t: setattr(ex.session_manager, "live", True))

    async def _ens(idx):
        return tab

    ex._ensure_warmup_tab = _ens
    out, _ = boot_run(ex, c)
    check("r1b_audit_stops_when_purchase_starts", out == "busy" and tab.deleted == ["staleA"]
          and "WON201" not in tab.deleted and tab.reads == 0, f"{out} {tab.deleted}")
    # The finding's sequence: a purchase's fast-lane ATC lands (WON201) between
    # the audit's read and the delete. A second read would include it.
    base = [{"id": "staleA", "tcin": OTHER, "qty": 4}, {"id": "staleB", "tcin": TCIN, "qty": 1}]
    reads = iter([base, base + [{"id": "WON201", "tcin": TCIN, "qty": 2}]])

    def _read_then_add():
        items = next(reads, base + [{"id": "WON201", "tcin": TCIN, "qty": 2}])
        return {"ok": True, "status": 200, "items": [dict(i) for i in items]}

    ex, wt = boot_ex(c, _read_then_add)
    del ex._delete_cart_items
    tab = RTab()

    async def _ens2(idx):
        return tab

    ex._ensure_warmup_tab = _ens2
    out, _ = boot_run(ex, c)
    check("r1b_audit_deletes_only_what_it_read", out == "deleted" and tab.deleted == ["staleA", "staleB"]
          and "WON201" not in tab.deleted and len(ex.reads) == 1, f"{out} {tab.deleted} {len(ex.reads)}")


def test_r1_held_marker_background_retire():
    """R1-ARM-4: an expired held marker is retired by the refill loop (warmup
    tab 0), not lazily in front of the next drop's ATC."""
    c = Clock()

    def mk(**kw):
        ex = bare(c)
        ex._warmup_pool_lock = None
        ex.ensured = []
        wt = types.SimpleNamespace(name="warmup0")

        async def _ens(idx):
            ex.ensured.append(idx)
            return wt

        ex._ensure_warmup_tab = _ens
        ex.session_manager.live = False
        ex._held_cart = dict({"tcin": TCIN, "qty": 2, "created": real_time.time() - 1000.0,
                              "tickets": 3, "verified": True}, **kw)
        return ex

    async def go(ex):
        ex._warmup_pool_lock = REAL_ASYNCIO.Lock()
        return await ex._held_cart_expire_tick()

    def tick(ex, flags=HELD):
        with in_tmp_cwd(), env(**flags):
            return run(go(ex))

    ex = mk()
    out = tick(ex)
    check("r1h_expired_retired", out == "retired" and ex._held_cart is None and ex.ensured == [0]
          and len(ex.deletes) == 1 and ex.deletes[0]["only"] == TCIN and callable(ex.deletes[0].get("abort"))
          and "[HELD_CART] retired in the background (ttl" in run.last_out, f"{out} {ex.deletes}")
    ex = mk(created=real_time.time() - 10.0)
    check("r1h_fresh_kept", tick(ex) == "" and ex._held_cart is not None and not ex.deletes)
    ex = mk(created=real_time.time() - 10.0, tickets=14)
    check("r1h_ticket_cap_retired", tick(ex) == "retired" and ex._held_cart is None)
    ex = mk()
    ex.delete_result = (False, 0)
    check("r1h_failed_delete_keeps_marker", tick(ex) == "release_failed" and ex._held_cart is not None
          and "marker kept" in run.last_out)
    ex = mk()
    ex.session_manager.live = True
    check("r1h_busy_no_action", tick(ex) == "busy" and not ex.deletes and ex._held_cart is not None)
    ex = mk()
    check("r1h_flag_off_no_action", tick(ex, flags=ARMED) == "" and not ex.deletes)
    # Refill loop wiring: tab 0 only, WC-3 only; BG-1 stretches the delay.
    for idx, flags, want_calls in ((0, HELD, 1), (1, HELD, 0), (0, ARMED, 0)):
        ex = bare(c)
        ex._warmup_pool_size = 2
        ex._warmup_in_progress = False
        ex._held_cart = {"tcin": TCIN, "created": 1.0}
        calls = []

        async def _refresh(i, force_fresh=False):
            return True

        async def _tick(_c=calls):
            _c.append(1)
            return "retired"

        ex._refresh_on_tab = _refresh
        ex._held_cart_expire_tick = _tick
        with env(**flags), fake_time(c, stop_after=1) as shim:
            run(ex._background_refill_loop(idx))
        check(f"r1h_refill_wiring[{idx},{len(flags)}]", len(calls) == want_calls, calls)
    for accounts, lo, hi in ((None, 60.0, 90.0), ("primary", 180.0, 270.0), ("alt-1", 60.0, 90.0)):
        ex = bare(c)
        ex._warmup_pool_size = 1
        ex._warmup_in_progress = True
        with env(TARGET_BG_SLOW_ACCOUNTS=accounts, TARGET_BG_SLOW_FACTOR="3"), fake_time(c, stop_after=1) as shim:
            run(ex._background_refill_loop(0))
        d = shim.sleeps[1] if len(shim.sleeps) > 1 else -1
        check(f"r1h_bg1_refill_delay[{accounts}]", lo <= d <= hi, shim.sleeps)


def test_r1_node_missing_fails():
    """R1-TEST-3: without node the ticket-JS / QG tests must FAIL, not skip."""
    global NODE
    saved = NODE
    n_failed = len(FAILED)
    NODE = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            test_a_ticket_js_node()
            test_b_qg_fast_lane()
    finally:
        NODE = saved
    new = FAILED[n_failed:]
    del FAILED[n_failed:]
    check("r1t_missing_node_is_a_failure",
          any(f.startswith("a_node_available") for f in new)
          and any(f.startswith("b_node_available") for f in new), new)


# ─────────────── review round R3 (2026-09-17, cart-safety review) ───────────────

FL_PO_503 = dict(FL_PO_FS, po={"status": 503, "body": "<html>gateway</html>", "fired": True})


def test_r3_po_5xx_unresolved():
    """WC-5XX-NOT-AMBIGUOUS: a received 408/5xx on a place-order does not prove
    'no order' (a gateway 504 can come back after the backend committed). A
    won-cart ticket that gets one ends terminal + ambiguous_commit (AC-1 latch),
    never held, never deleted, never re-raced."""
    for st in (500, 502, 503, 504, 408):
        for mode in ("pre_po", "po_only"):
            c = Clock()
            ex = bare(c)
            ex._checkout_reject_status, ex._checkout_reject_reason = 429, "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
            step = t_prepo(st, "<html>gw</html>") if mode == "pre_po" else t_po(st, "")
            tab = TicketTab(ex, [step, t_prepo(200, ORDER_BODY)], c)
            (v, r), _ = loop_run(ex, tab, c, FL_PRE429 if mode == "pre_po" else FL_PO_FS,
                                 TARGET_HELD_CART_REENTRY="1")
            check(f"r3p_5xx_terminal[{st},{mode}]",
                  v == "terminal" and r.get("ambiguous_commit") is True
                  and r.get("reason") == "checkout_navigation_failed" and r.get("success") is False
                  and [m for _, m, _ in tab.tickets] == [mode] and ex._po_ambiguous is True
                  and ex._woncart_po_unresolved is True and ex._held_cart is None and not ex.deletes
                  and not re.search(r"\d", str(r.get("error"))),
                  (v, r, [m for _, m, _ in tab.tickets], ex._held_cart, ex.deletes))
    # _api_checkout_ticket on its own: 504 keeps the place-order unresolved; 429 resolves it.
    for st, want in ((504, True), (429, False), (409, False)):
        c = Clock()
        ex = bare(c)
        tab = TicketTab(ex, [t_po(st, "")], c)
        L = ex._woncart_new_ledger(TCIN, 2, FL_PO_FS, c.t)
        with in_tmp_cwd(), env(**ARMED), fake_time(c):
            run(ex._api_checkout_ticket(tab, TCIN, 2, ex._ticket_headers_js(), "po_only", L))
        check(f"r3p_ticket_unresolved_flag[{st}]",
              ex._woncart_po_unresolved is want and ex._po_ambiguous is want,
              (ex._woncart_po_unresolved, ex._po_ambiguous))
    # The first fast-lane chain: AC-1 armed -> terminal + tagged; kill-switch or
    # AC-1 off -> the old fallthrough (flags-off behaviour unchanged).
    for label, e, want in (("ac1_on", {"TARGET_AMBIGUOUS_COMMIT_LATCH": "1", "TARGET_PO_5XX_AMBIGUOUS": None},
                            "terminal"),
                           ("killswitch", {"TARGET_AMBIGUOUS_COMMIT_LATCH": "1", "TARGET_PO_5XX_AMBIGUOUS": "0"},
                            "fallthrough"),
                           ("ac1_off", {"TARGET_AMBIGUOUS_COMMIT_LATCH": None, "TARGET_PO_5XX_AMBIGUOUS": None},
                            "fallthrough")):
        c = Clock()
        ex = bare(c)
        with env(**e), contextlib.redirect_stdout(io.StringIO()) as buf:
            v, term = ex._apply_fast_lane_result(json.loads(json.dumps(FL_PO_503)), TCIN, c.t)
        ok = v == want
        if want == "terminal":
            ok = ok and term.get("ambiguous_commit") is True and ex._po_ambiguous is True \
                and "place-order got HTTP 503" in buf.getvalue()
        else:
            ok = ok and term is None and ex._po_ambiguous is False
        check(f"r3p_fast_lane_5xx[{label}]", ok, (v, term, buf.getvalue()[-200:]))
    # The real call site: armed -> the loop never runs and the legacy path never runs.
    c = Clock()
    ex, tab = impl_ex(c, FL_PO_503)
    r = run_impl(ex, ARMED)
    check("r3p_call_site_5xx_terminal", r.get("reason") == "checkout_navigation_failed"
          and r.get("ambiguous_commit") is True and not ex.loop_calls and "checking_out" not in ex.statuses,
          f"{r} {ex.statuses}")
    ex, tab = impl_ex(c, FL_PO_503)
    try:
        run_impl(ex, {})
    except LegacyReached:
        pass
    check("r3p_call_site_flags_off_unchanged", "checking_out" in ex.statuses and not ex.loop_calls,
          ex.statuses)
    # Pure helper.
    u = pe_mod.po_status_unresolved
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_PO_5XX_AMBIGUOUS=None):
        check("r3p_unit_unresolved", all(u(s) for s in (500, 502, 503, 504, 599, 408, "504")))
        check("r3p_unit_resolved", not any(u(s) for s in (0, 200, 201, 400, 401, 403, 404, 409, 424, 429,
                                                          499, 600, None, "x", True)))
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH=None, TARGET_PO_5XX_AMBIGUOUS=None):
        check("r3p_unit_ac1_off", u(504) is False and u(504, ticket=True) is True and u(429, ticket=True) is False)
    with env(TARGET_AMBIGUOUS_COMMIT_LATCH="1", TARGET_PO_5XX_AMBIGUOUS="0"):
        check("r3p_unit_killswitch", u(504) is False and u(504, ticket=True) is True)


def test_r3_dirty_cart_flag_and_release():
    """WC-DIRTY-CART-LEGACY-CHECKOUT: an exit that may leave our line in the
    cart with no held marker flags it, and the next purchase deletes that line
    before anything fires (the FAST_SELLING cooldown sends it down the legacy
    path, which buys the whole cart)."""
    dirty = lambda e: getattr(e, "_woncart_dirty", None)  # noqa: E731
    # Terminal (no response): flagged, cart untouched, marker dropped, cooldown open.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(0, "")], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    d0 = dirty(ex)
    check("r3d_terminal_flagged", v == "terminal" and isinstance(d0, dict) and d0["tcin"] == TCIN
          and d0["why"] == "po_unresolved" and ex._held_cart is None and not ex.deletes
          and ex._fast_selling_until > c.t and "the next purchase deletes it" in run.last_out,
          (v, d0, ex._fast_selling_until - c.t))
    # qty_over: failed delete -> flagged; deleted -> clean.
    for ok_del in (True, False):
        c = Clock()
        ex = bare(c)
        ex.delete_result = (ok_del, 1 if ok_del else 0)
        tab = TicketTab(ex, [t_skip("cart_qty_over", qty=4)], c)
        (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
        check(f"r3d_qty_over[{ok_del}]", (dirty(ex) is not None) == (not ok_del)
              and r.get("reason") == ("cart_qty_cleared" if ok_del else "cart_qty_stuck"), (r, dirty(ex)))
    # cart_ticket_cap through the loop: failed delete -> flagged.
    for ok_del in (True, False):
        c = Clock()
        ex = bare(c)
        ex.delete_result = (ok_del, 1 if ok_del else 0)
        tab = TicketTab(ex, [t_pre(429)], c)
        (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1", TARGET_WONCART_MAX_TICKETS="1")
        check(f"r3d_cap[{ok_del}]", r.get("won_cart_exit") == "cart_ticket_cap"
              and (dirty(ex) is not None) == (not ok_del), (r, dirty(ex)))
    # _woncart_exit on its own (budget / WC-3 off / held / evicted).
    for reason, dl_off, ok_del, held, want in (
            ("cart_ticket_cap", 200.0, True, True, False),
            ("cart_ticket_cap", 200.0, False, True, True),
            ("cart_ticket_cap", 3.0, True, True, True),      # no budget: delete skipped
            ("qty_over", 3.0, True, True, True),
            ("tail_spent", 200.0, True, True, False),        # held for re-entry
            ("tail_spent", 200.0, True, False, False),       # WC-3 off: whole cart deleted
            ("tail_spent", 200.0, False, False, True),       # WC-3 off: delete failed
            ("tail_spent", 10.0, True, False, True),         # WC-3 off: no time to clear
            ("cart_evicted", 200.0, True, True, False)):
        c = Clock()
        ex = bare(c)
        ex.delete_result = (ok_del, 1 if ok_del else 0)
        st = {"reason": reason, "dl": c.t + dl_off}
        with in_tmp_cwd(), fake_time(c):
            run(ex._woncart_exit(TicketTab(ex, [], c), TCIN, {"tcin": TCIN, "tickets": 1}, st, c.t, held))
        check(f"r3d_exit[{reason},{dl_off:.0f},{ok_del},{held}]", bool(st.get("dirty")) == want, st)
    # Placed / held exits never flag.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(200, ORDER_BODY)], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1")
    check("r3d_placed_clean", v == "placed" and dirty(ex) is None, (v, dirty(ex)))
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(401)] * 3, c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429, TARGET_HELD_CART_REENTRY="1", TARGET_WONCART_CALL_MAX_S="280")
    check("r3d_held_clean", r.get("reason") == "won_cart_held" and dirty(ex) is None
          and isinstance(ex._held_cart, dict), (r, dirty(ex)))
    # A first-entry loop cancelled by the purchase timeout -> flagged.
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_pre(429)] * 3, c)
    try:
        loop_run(ex, tab, c, FL_PRE429, stop_after=0, TARGET_HELD_CART_REENTRY="1")
    except REAL_ASYNCIO.CancelledError:
        pass
    check("r3d_cancelled_first_flagged", not tab.tickets and isinstance(dirty(ex), dict)
          and dirty(ex)["why"] == "cancelled", dirty(ex))
    # ...a cancelled HELD entry keeps its marker and is not flagged.
    c = Clock()
    ex = bare(c)
    ex._held_cart = {"tcin": TCIN, "tickets": 2, "sched_used": 2, "verified": True,
                     "last_ticket_ts": c.t, "fs_seen_ts": 0.0, "cvv_put": "none", "created": c.t - 60}
    tab = TicketTab(ex, [t_po(429)], c)
    try:
        loop_run(ex, tab, c, None, entry="held", stop_after=0, TARGET_HELD_CART_REENTRY="1")
    except REAL_ASYNCIO.CancelledError:
        pass
    check("r3d_cancelled_held_not_flagged", isinstance(ex._held_cart, dict) and dirty(ex) is None,
          (ex._held_cart, dirty(ex)))

    # The review's repro: after the terminal exit above, the next purchase on
    # ANOTHER TCIN runs while the cooldown is open (legacy path). The left-over
    # line is deleted BEFORE the legacy ATC fires.
    def next_purchase(flag, delete_result=(True, 1), read=None, cooldown=True, flags=None):
        ex, tab = impl_ex(Clock(), FL_PRE429)
        ex._woncart_dirty = dict(flag) if flag is not None else None
        ex.delete_result = delete_result
        if cooldown:
            ex._fast_selling_until = real_time.time() + 30.0
        order = []
        orig_del = ex._delete_cart_items

        async def _del(t, **kw):
            order.append(("delete", kw.get("only_tcin")))
            return await orig_del(t, **kw)

        ex._delete_cart_items = _del
        ex.reads = []

        async def _read(t, timeout=2.5):
            ex.reads.append(timeout)
            return json.loads(json.dumps(read if read is not None else {"ok": True, "status": 200, "items": [
                {"id": "CI-1", "tcin": TCIN, "qty": 2}]}))

        ex._cart_items_read = _read

        async def _eval(js, await_promise=False):
            if "cart_items?field_groups" in js and "method: 'POST'" in js:
                order.append(("atc", None))
                return {"status": 201, "body": "{}", "cart_items": [{"tcin": OTHER, "quantity": 2}]}
            return None

        tab.evaluate = _eval
        e = dict(IMPL_ENV)
        e.update(HELD if flags is None else flags)
        res = None
        with in_tmp_cwd(), env(**e):
            try:
                res = run(ex._execute_purchase_impl(OTHER, quantity=2))
            except LegacyReached:
                res = None
        if "checking_out" in ex.statuses:
            res = "legacy"          # the stub raises there; the generic handler may swallow it
        return ex, res, order

    flag0 = dict(d0, ts=real_time.time())
    ex, res, order = next_purchase(flag0)
    check("r3d_release_before_legacy_atc", order[:2] == [("delete", TCIN), ("atc", None)]
          and res == "legacy" and ex.fl_calls == [] and dirty(ex) is None, (order, res, ex.fl_calls))
    # Cooldown over: released, then the fast lane (its cart gate) runs.
    ex, res, order = next_purchase(flag0, cooldown=False)
    check("r3d_release_before_fast_lane", order[:1] == [("delete", TCIN)] and ex.fl_calls == [2]
          and dirty(ex) is None, (order, ex.fl_calls))
    # Delete fails and the line is still there -> nothing fires, flag kept, tagged as no shot.
    ex, res, order = next_purchase(flag0, delete_result=(False, 0))
    check("r3d_release_failed_skips", isinstance(res, dict) and res.get("reason") == "held_cart_release_failed"
          and res.get("woncart_entry") == "held" and ("atc", None) not in order and ex.fl_calls == []
          and "checking_out" not in ex.statuses and dirty(ex) is not None
          and not re.search(r"\d", str(res.get("error"))), (res, order, dirty(ex)))
    # ...after the TTL the flag is dropped (this dispatch still skips).
    old = dict(flag0, ts=real_time.time() - 1000.0)
    ex, res, order = next_purchase(old, delete_result=(False, 0))
    check("r3d_release_failed_ttl_drops_flag", isinstance(res, dict)
          and res.get("reason") == "held_cart_release_failed" and dirty(ex) is None
          and ("atc", None) not in order, (res, dirty(ex)))
    # Delete fails but a read shows no line of ours (already deleted) -> continue.
    ex, res, order = next_purchase(flag0, delete_result=(False, 0),
                                   read={"ok": True, "status": 200, "items": [{"id": "CI-7", "tcin": OTHER,
                                                                               "qty": 1}]})
    check("r3d_release_already_gone_continues", ("atc", None) in order and dirty(ex) is None
          and res == "legacy", (res, order))
    # Works with WC-3 off too (WC-1 alone can leave a line behind).
    ex, res, order = next_purchase(flag0, flags=ARMED)
    check("r3d_release_wc3_off", order[:2] == [("delete", TCIN), ("atc", None)] and dirty(ex) is None, order)
    # No flag -> no read, no delete (flags-off path unchanged).
    ex, res, order = next_purchase(None)
    check("r3d_no_flag_no_delete", order[:1] == [("atc", None)] and not ex.reads, (order, ex.reads))
    # The boot audit never adopts a line that is pending release.
    c = Clock()
    one = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2}]}
    ex, wt = boot_ex(c, one)
    ex._woncart_dirty = dict(flag0)
    out, _ = boot_run(ex, c)
    check("r3d_boot_audit_skips_dirty", out == "dirty_pending" and ex._held_cart is None
          and not ex.reads and not ex.deletes, (out, ex._held_cart))
    ex, wt = boot_ex(c, lambda: (setattr(ex, "_woncart_dirty", dict(flag0)) or one))
    out, _ = boot_run(ex, c)
    check("r3d_boot_audit_dirty_during_read", out == "busy" and ex._held_cart is None, (out, ex._held_cart))


def test_r3_suspect_rereads_in_window():
    """R2-SUSPECT-READ-TOCTOU: a harvest add that lands AFTER a clean read is
    caught by the next po_only ticket's read (it used to be bought unread)."""
    c = Clock()
    ex = _r2_suspect_ex(c, orphan_age=301.0)
    ex._harvest_landed_suspect = True
    ex._harvest_landed_suspect_ts = c.t
    landed = {"ok": True, "status": 200, "items": [{"id": "CI-1", "tcin": TCIN, "qty": 2},
                                                   {"id": "CI-H", "tcin": "12345678", "qty": 1}]}
    tab = ReadTab(ex, [t_po(429, FS_BODY), t_skip("foreign_cart_item")], c, [R2_EXACT, landed])
    ex.delete_result = (True, 0)
    (v, r), _ = loop_run(ex, tab, c, FL_PO_FS, TARGET_WONCART_MAX_TICKETS="3")
    modes = [m for _, m, _ in tab.tickets]
    check("r3s_late_harvest_add_never_bought_unread", modes == ["po_only", "pre_po"]
          and len(tab.read_ts) == 2 and r.get("won_cart_exit") == "foreign_stuck"
          and all("const MODE = 'po_only'" not in js for _, _, js in tab.tickets[1:]),
          (modes, tab.read_ts, r))
    # Unit: the window rule.
    ex = bare(Clock())
    now = real_time.time()
    ex._harvest_landed_suspect = True
    for hts, cleared, want in ((now - 10, now - 5, "harvest_add"),       # read inside the window
                               (now - 10, 0.0, "harvest_add"),
                               (now - 400, now - 350, "harvest_add"),   # read at +50 s: still suspect
                               (now - 400, now - 50, ""),               # read at +350 s clears it
                               (now - 400, now - 100, "")):             # read exactly at +300 s
        ex._harvest_landed_suspect_ts = hts
        ex._woncart_suspect_cleared_ts = cleared
        ex._orphan_atc_ts = 0.0
        check(f"r3s_window_unit[{now - hts:.0f},{(cleared - hts) if cleared else -1:.0f}]",
              ex._woncart_cart_suspect() == want, ex._woncart_cart_suspect())
    ex._harvest_landed_suspect = False
    ex._woncart_suspect_cleared_ts = 0.0
    check("r3s_unset_flag_not_suspect", ex._woncart_cart_suspect() == "")
    src = Path(pe_mod.__file__).read_text(encoding="utf-8")
    check("r3s_comment_corrected", "The check-then-post\n" not in src.replace("\r\n", "\n")
          and "gap is the same as pre_po's" not in src)


class _Srv:
    """Fake cart server for the tick/dispatch race: DELETE of an id that is
    already gone answers 404."""

    def __init__(self, items):
        self.items = dict(items)
        self.log = []


class _SrvTab:
    def __init__(self, srv, name, lat_read=0.05, lat_delete=0.5):
        self.srv, self.name = srv, name
        self.lat_read, self.lat_delete = lat_read, lat_delete
        self.url = "https://www.target.com/"

    async def evaluate(self, js, await_promise=False, **kw):
        if "method: 'DELETE'" in js:
            cid = re.search(r"cart_items/([A-Za-z0-9_-]+)'", js).group(1)
            await REAL_ASYNCIO.sleep(self.lat_delete)
            st = 204 if self.srv.items.pop(cid, None) is not None else 404
            self.srv.log.append((self.name, "DELETE", cid, st))
            return st
        if "web_checkouts/v1/cart?cart_type=REGULAR" in js:
            await REAL_ASYNCIO.sleep(self.lat_read)
            its = [{"id": k, "tcin": v, "qty": 2} for k, v in self.srv.items.items()]
            self.srv.log.append((self.name, "READ", [i["id"] for i in its]))
            return {"ok": True, "status": 200, "items": its}
        raise AssertionError("unexpected evaluate: " + js[:80])


def test_r3_release_tolerates_double_delete():
    """CS-R1-TICK-DISPATCH-DOUBLE-DELETE: the background retire (warmup tab 0)
    and the dispatch-time retire delete the same expired line concurrently; the
    loser's 404 no longer skips that identity's shot."""
    def mk(srv, wt, fast_lane_calls):
        c = Clock(real_time.time())
        ex = bare(c)
        del ex._delete_cart_items                    # the real bounded delete
        ex._page_lock = None
        ex._warmup_pool_lock = None

        async def _ens(idx):
            return wt

        ex._ensure_warmup_tab = _ens
        ex.session_manager.live = False
        ex._held_cart = {"tcin": TCIN, "qty": 2, "created": real_time.time() - 1000.0,
                         "tickets": 3, "verified": True, "source": "first"}
        return ex

    async def race(ex, mt, start_delay):
        ex._page_lock = REAL_ASYNCIO.Lock()
        ex._warmup_pool_lock = REAL_ASYNCIO.Lock()
        tick = REAL_ASYNCIO.ensure_future(ex._held_cart_expire_tick())
        await REAL_ASYNCIO.sleep(start_delay)
        async with ex._page_lock:
            ex.session_manager.live = True
            res = await ex._held_cart_entry(mt, TCIN, 2, real_time.time())
            ex.session_manager.live = False
        return res, await tick

    srv = _Srv({"X1abc": TCIN})
    wt, mt = _SrvTab(srv, "tick"), _SrvTab(srv, "main")
    ex = mk(srv, wt, [])
    with in_tmp_cwd(), env(**dict(IMPL_ENV, **HELD)):
        res, tick = run(race(ex, mt, 0.10))
    dels = [e for e in srv.log if e[1] == "DELETE"]
    check("r3t_race_happened", len(dels) == 2 and sorted(d[3] for d in dels) == [204, 404], srv.log)
    check("r3t_dispatch_continues", res is None and ex._held_cart is None and not srv.items
          and "already deleted, continuing" in run.last_out, (res, run.last_out[-400:]))
    # A 404 while the line is really still there (read shows it) stays a failure.
    c = Clock()
    ex, tab = held_impl(c, mk_held(age=1000.0), read=EXACT_READ, probes={TCIN: True})
    ex.delete_result = (False, 0)
    r = run_impl(ex, HELD)
    check("r3t_line_still_there_fails", r.get("reason") == "held_cart_release_failed" and ex.fl_calls == []
          and len(ex.reads) == 1, (r, ex.reads))
    # A failed confirm read stays a failure.
    ex, tab = held_impl(c, mk_held(age=1000.0), read={"ok": False, "status": 503, "items": []},
                        probes={TCIN: True})
    ex.delete_result = (False, 0)
    r = run_impl(ex, HELD)
    check("r3t_read_failed_fails", r.get("reason") == "held_cart_release_failed" and ex.fl_calls == [], r)
    # Delete failed, read shows no line of ours -> the normal shot fires.
    ex, tab = held_impl(c, mk_held(age=1000.0), read={"ok": True, "status": 200, "items": []},
                        probes={TCIN: True})
    ex.delete_result = (False, 0)
    r = run_impl(ex, HELD)
    check("r3t_already_gone_shot_fires", ex.fl_calls == [2] and ex._held_cart is None, (r, ex.fl_calls))


def main():
    tests = (test_a_ticket_js_node, test_a_python_primitive, test_b_qg_fast_lane, test_c_call_site,
             test_c_hang_branch_placed, test_c_loop_core, test_c_loop_deadlines, test_c_loop_exits,
             test_c_reason_classification, test_c_quiet_mode, test_c_helpers, test_c_cart_read_delete,
             test_d_stock_snapshot, test_e_note_stock_read, test_f_held_cart_reentry,
             test_f_held_protections, test_f_boot_audit, test_f_new_reason_classification,
             test_g_wc2_impl, test_h_fl1_call_site,
             # review round R1 (2026-09-17)
             test_r1_yield_fleet_never_locks_out_held_reentry, test_r1_ride_kept_through_success_tail,
             test_r1_cvv_ledger_seed, test_r1_po_only_suspect_fallback, test_r1_page_global_removed,
             test_r1_boot_audit_delete_race, test_r1_held_marker_background_retire,
             test_r1_node_missing_fails,
             # review round R2 (2026-09-17)
             test_r2_po_only_suspect_read, test_r2_fl1_orphan_stamp_feeds_loop,
             test_r2_held_entry_tagged, test_r2_fs_ticket_ms_since_201,
             # review round R3 (2026-09-17, cart safety)
             test_r3_po_5xx_unresolved, test_r3_dirty_cart_flag_and_release,
             test_r3_suspect_rereads_in_window, test_r3_release_tolerates_double_delete)
    for fn in tests:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAIL:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

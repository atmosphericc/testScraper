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
      duplicate pre_checkout skip.

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

    async def _del(tab, only_tcin=None, keep_tcin=None, budget_s=15.0):
        ex.deletes.append({"only": only_tcin, "keep": keep_tcin, "budget": budget_s})
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
        print("[SKIP] node not found — ticket JS tests skipped")
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
    # Stage key: non-enumerable while running and after; entry deleted after
    # completion; the late read returns null (outcome unknown -> terminal).
    check("a_stage_key_non_enumerable", r["enumDuring"] is False and r["enumAfter"] is False
          and r["hasKeyAfter"] is True and r["hadKeyDuring"] is True, str(r))
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
        print("[SKIP] node not found — QG node tests skipped")
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
    c = Clock()
    ex = bare(c)
    tab = TicketTab(ex, [t_prepo(500, "{}")], c)
    (v, r), _ = loop_run(ex, tab, c, FL_PRE429)
    check("e_po_other_breaks", r.get("won_cart_exit") == "po_500", r)
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
    ex, tab = held_impl(c, mk_held(tcin=OTHER), probes={OTHER: False})
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
        check(f"b_not_clean_deleted[{label}]", out == "deleted" and ex._held_cart is None
              and ex.deletes == [{"only": None, "keep": None, "budget": 15.0}], f"{out} {ex.deletes}")
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


def main():
    tests = (test_a_ticket_js_node, test_a_python_primitive, test_b_qg_fast_lane, test_c_call_site,
             test_c_hang_branch_placed, test_c_loop_core, test_c_loop_deadlines, test_c_loop_exits,
             test_c_reason_classification, test_c_quiet_mode, test_c_helpers, test_c_cart_read_delete,
             test_d_stock_snapshot, test_e_note_stock_read, test_f_held_cart_reentry,
             test_f_held_protections, test_f_boot_audit, test_f_new_reason_classification,
             test_g_wc2_impl, test_h_fl1_call_site)
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

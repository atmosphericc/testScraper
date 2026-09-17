#!/usr/bin/env python3
"""Unit test: the ATC→pre_checkout→place-order fast lane (2026-07-21 drop fix).

WHY THIS EXISTS
---------------
run_20260720_231634.log went 0-for-9 with nine clean ATC 201s. The decisive
measurement: on wave 2 the ATC returned 201 at t=0.92s but the first place-order
POST did not leave until t=3.4s, and Target answered that otherwise-clean shot
429 RESERVATION_FAILURE. The 2.5s in between was `tab.get('/checkout/start')`
plus a DOM poll — pure dead time in which the inventory reservation died. Every
wave paid it (ATC→shot #1 measured 2.4-5.0s).

`_api_fast_lane` collapses ATC + pre_checkout + place-order into ONE in-browser
fetch chain (~0.85s, no navigation). Awaiting pre_checkout is what makes dropping
the nav safe — the 2026-05-07 attempt failed with 424
CART_COMPARISION_FAILURE_ERROR only because it raced an un-awaited pre_checkout.

The load-bearing properties are all about NOT double-buying and NOT firing a
place-order POST against a cart that isn't provably ours:
  * a 2xx is a committed order — never reported as failure, never re-shot
  * a fired-but-no-response POST is terminal, never retried
  * the chain must not reach the place-order POST at all when the ATC failed,
    the cart never hydrated, or a foreign item is in the cart

Half of that logic lives in JavaScript, so this test extracts the real JS the
executor sends and runs it under Node with a stubbed `fetch`, asserting on the
exact sequence of URLs actually requested. No browser, no network.

Run: python tests/test_fast_lane_checkout.py  (or: pytest tests/test_fast_lane_checkout.py)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TARGET_API_PLACE_ORDER", "true")
# 2026-09-16 hot-sku flags that change the fast-lane JS / timeout path: the
# original tests below describe the flags-OFF chain; Part 3 turns FL-1 on
# explicitly, per call.
_HOT_SKU_JS_FLAGS = ("TARGET_FASTLANE_STAGE_TRACK", "TARGET_FASTLANE_QTY_GUARD",
                     "TARGET_FASTLANE_T_STAMPS", "TARGET_FASTLANE_LOG_CART_QTY",
                     "TARGET_WONCART_DIRECT", "TARGET_HELD_CART_REENTRY",
                     "TARGET_AMBIGUOUS_COMMIT_LATCH")
for _k in _HOT_SKU_JS_FLAGS:
    os.environ.pop(_k, None)

import src.session.purchase_executor as pe_mod  # noqa: E402
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

TCIN = "94300072"
PASSED: list[str] = []
FAILED: list[str] = []


# ─────────────────────────────── harness ────────────────────────────────────

class _CapturingTab:
    """Captures the JS the executor would evaluate; returns a canned result."""

    def __init__(self, result=None, raises=None, hang=False):
        self.js = None
        self._result = result
        self._raises = raises
        self._hang = hang

    async def evaluate(self, js, await_promise=True):
        self.js = js
        if self._hang:
            await asyncio.sleep(30)
        if self._raises:
            raise self._raises
        return self._result


class _StubSessionManager:
    """Legacy single-account shape for _resolve_cvv (2026-08-11 named-account
    guard): no account_id and no per-account cvv, so resolution falls through
    to the module CARD_CVV default ('229') — the premise the CVV tests below
    were written against."""
    account_id = None

    def _load_account_cvv(self):
        return ''


def _bare_executor(cvv_required=False):
    ex = object.__new__(PurchaseExecutor)      # bypass heavy __init__
    ex.test_mode = False
    ex._api_order_id = None
    ex._api_confirmation_url = None
    ex._fastlane_placed = False
    ex._cvv_required = cvv_required
    ex._checkout_rejected = False
    ex._checkout_reject_reason = ""
    ex._checkout_reject_status = 0
    ex._fast_selling_until = 0.0
    ex._persist_cvv_challenge_flag = lambda: None
    ex.session_manager = _StubSessionManager()
    return ex


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


# ─────────────────── Part 1: the real JS, run under Node ────────────────────

NODE = shutil.which("node")

JS_HARNESS = r"""
const scenario = %s;
const calls = [];
globalThis.fetch = async (url, opts) => {
  calls.push({url: String(url).split('?')[0], method: opts && opts.method,
              body: (opts && opts.body) || null,
              hdr: (opts && opts.headers) ? Object.keys(opts.headers) : []});
  let rule = null;
  for (const r of scenario) {
    if (String(url).includes(r.match)) { rule = r; break; }
  }
  if (!rule) throw new Error('no stub rule for ' + url);
  // Optional per-call sequencing: rule.seq = [{status,body},...] consumed in
  // order (last repeats) — lets one URL answer differently across re-shoots.
  rule._n = (rule._n || 0) + 1;
  const eff = rule.seq ? rule.seq[Math.min(rule._n - 1, rule.seq.length - 1)] : rule;
  if (eff.throw) throw new Error(eff.throw);
  return { status: eff.status, text: async () => eff.body || '' };
};
(async () => {
  const out = await (%s);
  console.log(JSON.stringify({out, calls}));
})().catch(e => { console.log(JSON.stringify({error: String(e)})); });
"""


def run_js(scenario, cvv_required=False):
    """Run the executor's real fast-lane JS under Node with stubbed fetch."""
    ex = _bare_executor(cvv_required=cvv_required)
    tab = _CapturingTab(result={})
    asyncio.get_event_loop().run_until_complete(
        ex._api_fast_lane(tab, TCIN, 2, json.dumps({"X-GyJwza5Z-a": "tok"})))
    js = tab.js
    assert js, "no JS captured"
    src = JS_HARNESS % (json.dumps(scenario), js)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise AssertionError(f"node failed: {proc.stderr[:600]}")
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


ATC = "cart_items"
PRE = "pre_checkout"
PO = "v1/checkout"


def urls(res):
    return [c["url"].rsplit("/", 1)[-1] for c in res["calls"]]


def test_js_happy_path_places_order():
    ok_pre = json.dumps({"cart_items": [{"tcin": TCIN, "quantity": 2}],
                         "payment_instructions": [
                             {"payment_instruction_id": "PI-1",
                              "payment_type": "CARD", "cvv_required": False}]})
    r = run_js([
        {"match": ATC, "status": 201,
         "body": json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2})},
        {"match": PRE, "status": 200, "body": ok_pre},
        {"match": PO, "status": 200,
         "body": json.dumps({"orders": [{"order_id": "OID-9", "reference_id": "10203"}]})},
    ])
    out = res_out = r["out"]
    check("js_happy_path_fires_all_three_in_order",
          urls(r) == ["cart_items", "pre_checkout", "checkout"], str(urls(r)))
    check("js_happy_path_po_200", out["po"]["status"] == 200 and out["po"]["fired"] is True,
          str(out["po"]))
    check("js_happy_path_reports_atc_cart_items",
          out["atc"]["cart_items"] == [{"tcin": TCIN, "quantity": 2}],
          str(out["atc"]["cart_items"]))
    check("js_happy_path_extracts_payment_instruction_intel",
          res_out["pre"]["pi"] and res_out["pre"]["pi"][0]["id"] == "PI-1",
          str(res_out["pre"]["pi"]))


def test_js_bad_atc_never_reaches_place_order():
    r = run_js([
        {"match": ATC, "status": 401, "body": '{"errorKey":"_ERR_AUTH_DENIED"}'},
        {"match": PRE, "status": 200, "body": "{}"},
        {"match": PO, "status": 200, "body": "{}"},
    ])
    check("js_atc_401_stops_chain", urls(r) == ["cart_items"], str(urls(r)))
    check("js_atc_401_po_not_fired", r["out"]["po"]["fired"] is False, str(r["out"]["po"]))
    check("js_atc_401_skip_label", r["out"]["skip"] == "atc_401", r["out"]["skip"])


def test_js_unhydrated_cart_never_reaches_place_order():
    """The 2026-05-07 CART_COMPARISION_FAILURE_ERROR trap: no pre_checkout, no shot."""
    r = run_js([
        {"match": ATC, "status": 201,
         "body": json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2})},
        {"match": PRE, "status": 424, "body": ""},
        {"match": PO, "status": 200, "body": "{}"},
    ])
    check("js_pre_checkout_fail_stops_chain",
          urls(r) == ["cart_items", "pre_checkout"], str(urls(r)))
    check("js_pre_checkout_fail_po_not_fired",
          r["out"]["po"]["fired"] is False, str(r["out"]["po"]))


def test_js_foreign_cart_item_never_reaches_place_order():
    """Place-order buys the WHOLE cart — a leaked item must abort the shot."""
    r = run_js([
        {"match": ATC, "status": 201,
         "body": json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2})},
        {"match": PRE, "status": 200,
         "body": json.dumps({"cart_items": [{"tcin": TCIN}, {"tcin": "99999999"}]})},
        {"match": PO, "status": 200, "body": "{}"},
    ])
    check("js_foreign_cart_item_stops_chain",
          urls(r) == ["cart_items", "pre_checkout"], str(urls(r)))
    check("js_foreign_cart_item_skip_label",
          r["out"]["skip"] == "foreign_cart_item", r["out"]["skip"])


def test_js_place_order_throw_is_reported_as_fired():
    """A thrown place-order fetch MUST still report fired=True (may have committed)."""
    r = run_js([
        {"match": ATC, "status": 201,
         "body": json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2})},
        {"match": PRE, "status": 200, "body": json.dumps({"cart_items": [{"tcin": TCIN}]})},
        {"match": PO, "throw": "network down"},
    ])
    check("js_po_throw_marks_fired",
          r["out"]["po"]["fired"] is True and r["out"]["po"]["status"] == 0,
          str(r["out"]["po"]))


def test_js_atc_throw_does_not_mark_fired():
    r = run_js([
        {"match": ATC, "throw": "boom"},
        {"match": PRE, "status": 200, "body": "{}"},
        {"match": PO, "status": 200, "body": "{}"},
    ])
    check("js_atc_throw_po_not_fired", r["out"]["po"]["fired"] is False, str(r["out"]["po"]))
    check("js_atc_throw_skip_label", r["out"]["skip"] == "atc_threw", r["out"]["skip"])


# ─────────── Part 1b: the in-lane CVV step (Endpoint 8, 2026-07-28) ──────────
# Captured live on 07-24 AND 07-28 (byte-identical shape): the checkout page's
# CVV modal answers MISSING_CREDIT_CARD_CVV with
#   PUT /checkout_payments/v1/payment_instructions/{id}?key=...
#   {"card_details":{"cvv":"..."},"cart_id":"...","payment_type":"CARD",
#    "wallet_mode":"NONE"}          (NO Shape X-headers on the real request)
# and the next place-order stops 400ing. The bare executor has no
# session_manager, so the chain embeds the module-default CVV ('229').

CVVPUT = "payment_instructions/"

_PRE_OK_CVV = json.dumps({"cart_id": "CART-77",
                          "cart_items": [{"tcin": TCIN, "quantity": 2}],
                          "payment_instructions": [
                              {"payment_instruction_id": "PI-1",
                               "payment_type": "CARD"}]})
_ATC_OK_CVV = json.dumps({"tcin": TCIN, "cart_item_id": "CI-1", "quantity": 2,
                          "cart_id": "CART-77"})


def test_js_cvv_400_put_then_reshoot_wins():
    """The 07-28 loss replayed to a win: po 400 → CVV PUT → re-shoot → 200."""
    r = run_js([
        {"match": ATC, "status": 201, "body": _ATC_OK_CVV},
        {"match": PRE, "status": 200, "body": _PRE_OK_CVV},
        {"match": CVVPUT, "status": 200, "body": "{}"},
        {"match": PO, "seq": [
            {"status": 400, "body": ""},
            {"status": 200,
             "body": json.dumps({"orders": [{"order_id": "OID-CVV"}]})}]},
    ])
    out = r["out"]
    check("cvv_recovery_call_order",
          urls(r) == ["cart_items", "pre_checkout", "checkout", "PI-1", "checkout"],
          str(urls(r)))
    check("cvv_recovery_final_po_200",
          out["po"]["status"] == 200 and out["po"]["fired"] is True, str(out["po"]))
    check("cvv_recovery_reports_reshot",
          out["cvv"]["reshot"] is True and out["cvv"]["po1"] == 400, str(out["cvv"]))
    put_call = [c for c in r["calls"] if CVVPUT in c["url"]][0]
    body = json.loads(put_call["body"])
    check("cvv_put_body_matches_capture",
          body == {"card_details": {"cvv": "229"}, "cart_id": "CART-77",
                   "payment_type": "CARD", "wallet_mode": "NONE"}, str(body))
    check("cvv_put_is_PUT_without_shape_headers",
          put_call["method"] == "PUT"
          and not any(h.startswith("X-GyJwza5Z") for h in put_call["hdr"]),
          str(put_call))


def test_js_latched_account_pre_puts_before_po():
    """Latched account stays IN-LANE: PUT fires between pre_checkout and po."""
    r = run_js([
        {"match": ATC, "status": 201, "body": _ATC_OK_CVV},
        {"match": PRE, "status": 200, "body": _PRE_OK_CVV},
        {"match": CVVPUT, "status": 200, "body": "{}"},
        {"match": PO, "status": 200,
         "body": json.dumps({"orders": [{"order_id": "OID-L"}]})},
    ], cvv_required=True)
    out = r["out"]
    check("latched_pre_put_call_order",
          urls(r) == ["cart_items", "pre_checkout", "PI-1", "checkout"],
          str(urls(r)))
    check("latched_pre_put_reported",
          out["cvv"]["first"] is True and out["cvv"]["put"] == 200
          and out["cvv"]["reshot"] is False, str(out["cvv"]))
    check("latched_po_200", out["po"]["status"] == 200, str(out["po"]))


def test_js_cvv_put_failure_no_reshoot():
    """A failed PUT must NOT re-shoot — the 400 falls through to the DOM path."""
    r = run_js([
        {"match": ATC, "status": 201, "body": _ATC_OK_CVV},
        {"match": PRE, "status": 200, "body": _PRE_OK_CVV},
        {"match": CVVPUT, "status": 500, "body": ""},
        {"match": PO, "status": 400, "body": ""},
    ])
    out = r["out"]
    check("cvv_put_fail_call_order",
          urls(r) == ["cart_items", "pre_checkout", "checkout", "PI-1"],
          str(urls(r)))
    check("cvv_put_fail_po_stays_400",
          out["po"]["status"] == 400 and out["cvv"]["reshot"] is False,
          f"po={out['po']['status']} cvv={out['cvv']}")


def test_js_po_429_does_not_trigger_cvv_put():
    """The CVV recovery keys on 400 ONLY — a 429 must not spend a PUT."""
    r = run_js([
        {"match": ATC, "status": 201, "body": _ATC_OK_CVV},
        {"match": PRE, "status": 200, "body": _PRE_OK_CVV},
        {"match": CVVPUT, "status": 200, "body": "{}"},
        {"match": PO, "status": 429, "body": ""},
    ])
    check("po_429_no_cvv_put",
          urls(r) == ["cart_items", "pre_checkout", "checkout"], str(urls(r)))
    check("po_429_cvv_untouched", r["out"]["cvv"]["put"] == -1,
          str(r["out"]["cvv"]))


# ──────────────── Part 2: Python-side result handling ───────────────────────

def _fl(po_status=0, po_body="", fired=True, skip="", atc_status=201):
    return {"atc": {"status": atc_status, "body": "", "cart_items": []},
            "pre": {"status": 200}, "skip": skip,
            "po": {"status": po_status, "body": po_body, "fired": fired}}


def test_success_marks_placed_and_parses_order_id():
    ex = _bare_executor()
    body = json.dumps({"orders": [{"order_id": "OID-42", "reference_id": "1020"}]})
    verdict, term = ex._apply_fast_lane_result(_fl(200, body), TCIN, 0.0)
    check("success_verdict_placed", verdict == "placed", verdict)
    check("success_sets_fastlane_placed", ex._fastlane_placed is True)
    check("success_parses_order_id", ex._api_order_id == "OID-42", str(ex._api_order_id))


def test_success_with_unparseable_body_still_succeeds():
    """A 2xx is a committed order. Never report failure just because parsing failed."""
    ex = _bare_executor()
    verdict, _ = ex._apply_fast_lane_result(_fl(200, "<not json>"), TCIN, 0.0)
    check("unparseable_2xx_still_placed", verdict == "placed", verdict)
    check("unparseable_2xx_has_synthetic_id",
          bool(ex._api_order_id) and ex._api_order_id.startswith("UNPARSED-"),
          str(ex._api_order_id))


def test_no_response_is_terminal_and_never_retryable():
    ex = _bare_executor()
    verdict, term = ex._apply_fast_lane_result(
        _fl(0, "", fired=True, skip="evaluate_timeout"), TCIN, 0.0)
    check("no_response_verdict_terminal", verdict == "terminal", verdict)
    check("no_response_not_success", term and term["success"] is False)
    # 'checkout_navigation_failed' is the manager's non-retryable reason;
    # 'checkout_busy_retryable' would re-race and risk a second order.
    check("no_response_reason_is_non_retryable",
          term["reason"] == "checkout_navigation_failed", str(term.get("reason")))
    check("no_response_does_not_mark_placed", ex._fastlane_placed is False)


def test_cvv_400_latches_and_falls_through():
    ex = _bare_executor()
    ex._checkout_reject_status = 400
    ex._checkout_reject_reason = "MISSING_CREDIT_CARD_CVV"
    verdict, _ = ex._apply_fast_lane_result(_fl(400, ""), TCIN, 0.0)
    check("cvv_400_falls_through", verdict == "fallthrough", verdict)
    check("cvv_400_latches_cvv_required", ex._cvv_required is True)
    check("cvv_400_does_not_mark_placed", ex._fastlane_placed is False)


def test_fast_selling_429_starts_cooldown():
    ex = _bare_executor()
    ex._checkout_reject_status = 429
    ex._checkout_reject_reason = "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"
    verdict, _ = ex._apply_fast_lane_result(_fl(429, ""), TCIN, 0.0)
    check("fast_selling_falls_through", verdict == "fallthrough", verdict)
    check("fast_selling_starts_cooldown", ex._fast_selling_cooling_down() is True)


def test_reservation_failure_falls_through_without_cooldown():
    ex = _bare_executor()
    ex._checkout_reject_status = 429
    ex._checkout_reject_reason = "RESERVATION_FAILURE"
    verdict, _ = ex._apply_fast_lane_result(_fl(429, ""), TCIN, 0.0)
    check("reservation_failure_falls_through", verdict == "fallthrough", verdict)
    check("reservation_failure_no_cooldown", ex._fast_selling_cooling_down() is False)


def test_chain_stopped_before_po_falls_through():
    ex = _bare_executor()
    verdict, _ = ex._apply_fast_lane_result(
        _fl(0, "", fired=False, skip="atc_401", atc_status=401), TCIN, 0.0)
    check("stopped_chain_falls_through", verdict == "fallthrough", verdict)
    check("stopped_chain_not_placed", ex._fastlane_placed is False)


def test_evaluate_timeout_reports_fired():
    """A hung evaluate cannot prove the POST didn't commit ⇒ must report fired."""
    ex = _bare_executor()
    tab = _CapturingTab(hang=True)

    async def go():
        return await ex._api_fast_lane(tab, TCIN, 2, "{}")

    # _api_fast_lane's own asyncio.wait_for(timeout=12.0) is what we're testing;
    # patch it down so the test is fast.
    real_wait_for = asyncio.wait_for

    async def quick_wait_for(aw, timeout):
        return await real_wait_for(aw, 0.05)

    asyncio.wait_for = quick_wait_for
    try:
        res = asyncio.get_event_loop().run_until_complete(go())
    finally:
        asyncio.wait_for = real_wait_for
    check("timeout_marks_po_fired", res["po"]["fired"] is True, str(res["po"]))
    check("timeout_verdict_terminal",
          ex._apply_fast_lane_result(res, TCIN, 0.0)[0] == "terminal")


def test_evaluate_raise_reports_fired():
    ex = _bare_executor()
    tab = _CapturingTab(raises=RuntimeError("Inspected target navigated or closed"))
    res = asyncio.get_event_loop().run_until_complete(
        ex._api_fast_lane(tab, TCIN, 2, "{}"))
    check("raise_marks_po_fired", res["po"]["fired"] is True, str(res["po"]))
    check("raise_verdict_terminal",
          ex._apply_fast_lane_result(res, TCIN, 0.0)[0] == "terminal")


def test_fast_selling_cooldown_can_be_disabled():
    ex = _bare_executor()
    os.environ["TARGET_FAST_SELLING_COOLDOWN_S"] = "0"
    try:
        ex._note_fast_selling_throttle()
        check("cooldown_kill_switch", ex._fast_selling_cooling_down() is False)
    finally:
        os.environ.pop("TARGET_FAST_SELLING_COOLDOWN_S", None)


def test_fast_lane_cvv_source_and_validation():
    ex = _bare_executor()
    # bare instance has no session_manager → module default CARD_CVV ('229')
    check("cvv_falls_back_to_module_default", ex._fast_lane_cvv() == "229",
          repr(ex._fast_lane_cvv()))

    class _SM:
        def _load_account_cvv(self):
            return "814"

    ex.session_manager = _SM()
    check("cvv_prefers_account_value", ex._fast_lane_cvv() == "814",
          repr(ex._fast_lane_cvv()))

    class _SMBad:
        def _load_account_cvv(self):
            return "12ab"

    ex.session_manager = _SMBad()
    check("cvv_rejects_non_digits", ex._fast_lane_cvv() == "",
          repr(ex._fast_lane_cvv()))
    os.environ["TARGET_FAST_LANE_CVV"] = "0"
    try:
        ex.session_manager = _SM()
        check("cvv_kill_switch_returns_empty", ex._fast_lane_cvv() == "",
              repr(ex._fast_lane_cvv()))
    finally:
        os.environ.pop("TARGET_FAST_LANE_CVV", None)


def test_placed_via_cvv_reshoot_latches_for_pre_put():
    """An in-lane CVV recovery win must latch so the NEXT chain pre-PUTs."""
    ex = _bare_executor()
    persisted = []
    ex._persist_cvv_challenge_flag = lambda: persisted.append(1)
    fl = _fl(200, json.dumps({"orders": [{"order_id": "OID-9"}]}))
    fl["cvv"] = {"put": 200, "first": False, "reshot": True, "po1": 400}
    verdict, _ = ex._apply_fast_lane_result(fl, TCIN, 0.0)
    check("cvv_reshoot_win_placed", verdict == "placed", verdict)
    check("cvv_reshoot_win_latches",
          ex._cvv_required is True and len(persisted) == 1,
          f"cvv_required={ex._cvv_required} persisted={len(persisted)}")


def test_hold_cart_for_fast_selling():
    import time as _t
    loop = asyncio.get_event_loop()
    ex = _bare_executor()
    ex._fast_selling_until = 0.0
    check("hold_no_cooldown_returns_false",
          loop.run_until_complete(ex._hold_cart_for_fast_selling("t")) is False)
    ex._fast_selling_until = _t.time() + 0.05
    t0 = _t.time()
    r = loop.run_until_complete(ex._hold_cart_for_fast_selling("t"))
    check("hold_active_cooldown_sleeps_and_returns_true",
          r is True and _t.time() - t0 >= 0.04, f"r={r} dt={_t.time()-t0:.3f}")
    os.environ["TARGET_FAST_SELLING_HOLD_CART"] = "0"
    try:
        ex._fast_selling_until = _t.time() + 5
        check("hold_kill_switch_returns_false",
              loop.run_until_complete(ex._hold_cart_for_fast_selling("t")) is False)
    finally:
        os.environ.pop("TARGET_FAST_SELLING_HOLD_CART", None)
    os.environ["TARGET_FAST_SELLING_HOLD_MAX_S"] = "0.05"
    try:
        ex._fast_selling_until = _t.time() + 60
        t0 = _t.time()
        r = loop.run_until_complete(ex._hold_cart_for_fast_selling("t"))
        check("hold_respects_cap", r is True and _t.time() - t0 < 2.0,
              f"dt={_t.time()-t0:.2f}")
    finally:
        os.environ.pop("TARGET_FAST_SELLING_HOLD_MAX_S", None)


# ───── Part 3: FL-1 stage tracking with atomic read-and-abort (2026-09-16) ─────
# 09-16 03:46:01: an alt-1 shot was ended as a possible double-buy although no
# place-order had gone out — a 12 s evaluate timeout cannot tell where the page
# chain is. TARGET_FASTLANE_STAGE_TRACK=1 registers {s, atc, abort} under a
# per-run random NON-enumerable page global; before each post-ATC fetch the
# chain checks `abort` and writes its stage in the same synchronous run. On a
# timeout ONE evaluate reads the stage and sets abort unless it is already 'po'.
# The load-bearing property proven here under Node: once the read has returned
# aborted:true, NO later fetch can start — in particular never the place-order.

import contextlib  # noqa: E402
import re  # noqa: E402

STAGE_HARNESS = r"""
const S = %s;
const calls = [];
const pend = [];
const KEYRE = /^__[0-9a-f]{12}$/;
const stageKeys = () => Object.getOwnPropertyNames(globalThis).filter(k => KEYRE.test(k));
const keysBefore = stageKeys();
globalThis.fetch = (url, opts) => {
  const u = String(url);
  calls.push({url: u.split('?')[0], method: (opts && opts.method) || 'GET'});
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
const tick = () => new Promise(r => setTimeout(r, 2));
(async () => {
  const p = (%s);
  const res = {abortRead: null, callsAtAbort: null};
  if (S.abort) {
    for (let i = 0; i < 1000 && pend.length === 0; i++) await tick();
    res.pendingAtAbort = pend.length;
    res.enumDuring = Object.keys(globalThis).includes(S.key);
    let fi = false;
    for (const k in globalThis) { if (k === S.key) fi = true; }
    res.forInDuring = fi;
    res.jsonDuring = JSON.stringify(Object.keys(globalThis)).includes(S.key);
    const ent = globalThis[S.key] && globalThis[S.key][S.n];
    res.stageDuring = ent ? ent.s : null;
    // resolveFirst: the pending response is IN (promise resolved) but the
    // chain's continuation has not run yet when the read executes.
    if (S.resolveFirst) { while (pend.length) pend.shift()(); }
    res.callsAtAbort = calls.length;
    res.abortRead = (%s);
    while (pend.length) pend.shift()();
  }
  const out = await p;
  for (let i = 0; i < 10; i++) { while (pend.length) pend.shift()(); await tick(); }
  res.lateRead = (%s);
  res.out = out;
  res.calls = calls;
  res.keysBefore = keysBefore;
  res.stageKeysAfter = stageKeys();
  res.enumAfter = Object.keys(globalThis).includes(S.key);
  res.hasKeyAfter = Object.prototype.hasOwnProperty.call(globalThis, S.key);
  res.entriesAfter = globalThis[S.key] ? Object.getOwnPropertyNames(globalThis[S.key]) : null;
  console.log(JSON.stringify(res));
})().catch(e => { console.log(JSON.stringify({error: String(e && e.stack || e)})); });
"""

_PO_ORDER = json.dumps({"orders": [{"order_id": "OID-FL1", "reference_id": "1"}]})
_TMPDIR = tempfile.mkdtemp(prefix="fl1_")


@contextlib.contextmanager
def _env(**kv):
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


def _loop_run(coro):
    """Own event loop (never touches the default loop the older tests use)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _render_chain(stage_track, cvv_required=False, **flags):
    """(js, key, n) of the real chain; key/n are '' when FL-1 is off."""
    ex = _bare_executor(cvv_required=cvv_required)
    tab = _CapturingTab(result={})
    with _env(TARGET_FASTLANE_STAGE_TRACK="1" if stage_track else None, **flags):
        _loop_run(ex._api_fast_lane(tab, TCIN, 2, json.dumps({"X-GyJwza5Z-a": "tok"})))
    key = getattr(ex, "_fl_stage_key", "") if stage_track else ""
    n = f"f{getattr(ex, '_fl_stage_seq', 0)}" if stage_track else ""
    return tab.js, key, n


def _stage_node(js, key, n, rules, abort=False, resolve_first=False):
    ab = pe_mod.render_fast_lane_abort_js(key or "__000000000000", n or "f1")
    scen = {"rules": rules, "abort": abort, "resolveFirst": resolve_first,
            "key": key or "__000000000000", "n": n or "f1"}
    src = STAGE_HARNESS % (json.dumps(scen), js, ab, ab)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8", dir=_TMPDIR) as f:
        f.write(src)
        path = f.name
    try:
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"node failed rc={proc.returncode}: {proc.stderr[:600]}")
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    if "error" in res:
        raise AssertionError("node harness error: " + res["error"][:600])
    return res


def _kinds(res, upto=None):
    out = []
    for c in res["calls"][:upto]:
        u = c["url"]
        if "pre_checkout" in u:
            out.append("pre")
        elif "payment_instructions" in u:
            out.append("put")
        elif u.endswith("/v1/checkout"):
            out.append("po")
        elif u.endswith("/cart_items"):
            out.append("atc")
        else:
            out.append(u)
    return out


def _rules(atc=None, pre=None, put=None, po=None):
    """Default: 201 add, exact cart (qty 2, PI-1), PUT 200, place-order 200."""
    return [
        dict({"match": ATC, "status": 201, "body": _ATC_OK_CVV}, **(atc or {})),
        dict({"match": PRE, "status": 200, "body": _PRE_OK_CVV}, **(pre or {})),
        dict({"match": CVVPUT, "status": 200, "body": "{}"}, **(put or {})),
        dict({"match": PO, "status": 200, "body": _PO_ORDER}, **(po or {})),
    ]


def _clean_after(res):
    """Entry deleted in finally, key never enumerable, late read = null, and
    (R1 review, R1-PAGE-GLOBAL-PERSISTS) the container itself is gone once its
    last entry is: no stage-key global is left on the page at all, not even
    for Object.getOwnPropertyNames / the 'in' operator."""
    return (res["entriesAfter"] is None and res["enumAfter"] is False
            and res["lateRead"] is None and res["hasKeyAfter"] is False
            and res["stageKeysAfter"] == [] and res["keysBefore"] == [])


def test_fl1_js_structure_and_flag_off():
    off, _, _ = _render_chain(False)
    on, key, n = _render_chain(True)
    check("fl1_off_creates_nothing_in_js", "const _SE" not in off and "_SE." not in off
          and "const _SK" not in off and "defineProperty" not in off, "")
    check("fl1_key_and_id_shape", re.fullmatch(r"__[0-9a-f]{12}", key or "") is not None
          and re.fullmatch(r"f\d+", n or "") is not None, f"{key} {n}")
    frags = pe_mod.render_fast_lane_stage_fragments(key, n)
    stripped = on
    for name in ("open", "atc", "pre", "cvv", "po", "close"):
        check(f"fl1_fragment_present_once[{name}]", stripped.count(frags[name]) == 1,
              stripped.count(frags[name]))
        stripped = stripped.replace(frags[name], "", 1)
    check("fl1_on_minus_fragments_equals_off", stripped == off,
          next((f"line {i + 1}: {a!r} vs {b!r}" for i, (a, b)
                in enumerate(zip(stripped.split("\n"), off.split("\n"))) if a != b), "length"))
    # Every stage write is immediately followed (synchronously) by its fetch:
    # between the stage write and the next `fetch(` there is no `await`.
    for stage in ("pre", "cvv", "po"):
        i = on.index(f"_SE.s = '{stage}';")
        j = on.index("fetch(", i)
        seg = re.sub(r"await\s*$", "", on[i:j])      # the fetch's own `await` is fine
        check(f"fl1_no_await_between_stage_and_fetch[{stage}]", "await" not in seg, seg[:200])
    # The in-lane 3b re-shoot / reactive PUT is after stage 'po' and unchecked.
    po_i = on.index("_SE.s = 'po';")
    check("fl1_3b_after_po_stage", on.index("out.cvv.reshot = true;") > po_i
          and on.count("if (_SE.abort)") == 3, on.count("if (_SE.abort)"))
    # Two chains on one executor: same key, fresh entry id.
    ex = _bare_executor()
    tab = _CapturingTab(result={})
    with _env(TARGET_FASTLANE_STAGE_TRACK="1"):
        _loop_run(ex._api_fast_lane(tab, TCIN, 2, "{}"))
        js1 = tab.js
        _loop_run(ex._api_fast_lane(tab, TCIN, 2, "{}"))
        js2 = tab.js
    k1 = re.search(r"const _SK = '(__[0-9a-f]{12})'", js1).group(1)
    k2 = re.search(r"const _SK = '(__[0-9a-f]{12})'", js2).group(1)
    n1 = re.search(r"const _SN = '(f\d+)'", js1).group(1)
    n2 = re.search(r"const _SN = '(f\d+)'", js2).group(1)
    check("fl1_key_per_run_entry_per_chain", k1 == k2 and n1 != n2, f"{k1} {k2} {n1} {n2}")
    # Renderer validation.
    bad = 0
    for k, nn in (("__x", "f1"), ("__0123456789ab", "t1"), ("__0123456789ab", "f"),
                  ("__0123456789ab'", "f1"), ("x__0123456789ab", "f1")):
        for fn in (pe_mod.render_fast_lane_stage_fragments, pe_mod.render_fast_lane_abort_js):
            try:
                fn(k, nn)
            except ValueError:
                bad += 1
    check("fl1_renderers_reject_bad_key_or_id", bad == 10, bad)
    ab = pe_mod.render_fast_lane_abort_js("__0123456789ab", "f7")
    check("fl1_abort_js_rendered", "@@" not in ab and "['__0123456789ab']" in ab and "['f7']" in ab)
    # Renderer failure -> untracked chain (byte-identical to flag off).
    real = pe_mod.render_fast_lane_stage_fragments

    def _boom(*a, **k):
        raise ValueError("x")

    pe_mod.render_fast_lane_stage_fragments = _boom
    try:
        fb, _, _ = _render_chain(True)
    finally:
        pe_mod.render_fast_lane_stage_fragments = real
    check("fl1_render_failure_falls_back_to_off_js", fb == off)


def test_fl1_node_abort_protocol():
    on, key, n = _render_chain(True)
    on_cvv, key_c, n_c = _render_chain(True, cvv_required=True)

    # Happy path with tracking: same three requests, order placed, entry gone.
    r = _stage_node(on, key, n, _rules())
    check("fl1_node_happy_path", _kinds(r) == ["atc", "pre", "po"] and r["out"]["po"]["status"] == 200
          and r["out"]["skip"] == "", f"{_kinds(r)} {r['out']['skip']}")
    check("fl1_node_happy_path_cleanup", _clean_after(r), str(r))

    # (1) Parked on the ATC -> abort -> ATC resolves 201 -> NO pre, NO po.
    for rf in (False, True):
        r = _stage_node(on, key, n, _rules(atc={"hang": True}), abort=True, resolve_first=rf)
        check(f"fl1_abort_during_atc[resolve_first={rf}]",
              r["abortRead"] == {"s": "atc", "aborted": True, "atc": 0}
              and r["callsAtAbort"] == 1 and _kinds(r) == ["atc"]
              and r["out"]["skip"] == "aborted" and r["out"]["po"]["fired"] is False,
              f"{r['abortRead']} {_kinds(r)} {r['out']['skip']}")
        check(f"fl1_abort_during_atc_cleanup[{rf}]", _clean_after(r), str(r))
        check(f"fl1_key_hidden_while_running[{rf}]", r["enumDuring"] is False
              and r["forInDuring"] is False and r["jsonDuring"] is False
              and r["stageDuring"] == "atc", str(r))
    # Aborted during the ATC but the ATC comes back 401: the chain's own
    # atc_401 exit wins; still nothing after it.
    r = _stage_node(on, key, n, _rules(atc={"hang": True, "status": 401, "body": "{}"}), abort=True)
    check("fl1_abort_during_atc_then_401", _kinds(r) == ["atc"] and r["out"]["skip"] == "atc_401"
          and _clean_after(r), f"{_kinds(r)} {r['out']['skip']}")

    # (2) Parked on pre_checkout -> abort -> pre resolves 200 on an EXACT cart
    # -> NO place-order (and no CVV PUT on a latched account: cvvPut reports
    # -2 = aborted, not fired).
    for label, js, k, nn, put in (("unlatched", on, key, n, -1), ("latched", on_cvv, key_c, n_c, -2)):
        for rf in (False, True):
            r = _stage_node(js, k, nn, _rules(pre={"hang": True}), abort=True, resolve_first=rf)
            check(f"fl1_abort_during_pre[{label},resolve_first={rf}]",
                  r["abortRead"] == {"s": "pre", "aborted": True, "atc": 201}
                  and r["callsAtAbort"] == 2 and _kinds(r) == ["atc", "pre"]
                  and r["out"]["skip"] == "aborted" and r["out"]["po"]["fired"] is False
                  and r["out"]["cvv"]["put"] == put,
                  f"{r['abortRead']} {_kinds(r)} {r['out']['skip']} {r['out']['cvv']}")
            check(f"fl1_abort_during_pre_cleanup[{label},{rf}]", _clean_after(r), str(r))

    # (3) Parked on the latched CVV PUT -> abort -> PUT resolves 200 -> NO po.
    for rf in (False, True):
        r = _stage_node(on_cvv, key_c, n_c, _rules(put={"hang": True}), abort=True, resolve_first=rf)
        check(f"fl1_abort_during_cvv[resolve_first={rf}]",
              r["abortRead"] == {"s": "cvv", "aborted": True, "atc": 201}
              and r["callsAtAbort"] == 3 and _kinds(r) == ["atc", "pre", "put"]
              and r["out"]["skip"] == "aborted" and r["out"]["po"]["fired"] is False,
              f"{r['abortRead']} {_kinds(r)} {r['out']['skip']}")
        check(f"fl1_abort_during_cvv_cleanup[{rf}]", _clean_after(r), str(r))

    # (4) Parked on the place-order -> the read REFUSES (the POST may be on the
    # wire) and the chain completes with the real response.
    for label, js, k, nn, want in (("unlatched", on, key, n, ["atc", "pre", "po"]),
                                   ("latched", on_cvv, key_c, n_c, ["atc", "pre", "put", "po"])):
        for rf in (False, True):
            r = _stage_node(js, k, nn, _rules(po={"hang": True}), abort=True, resolve_first=rf)
            check(f"fl1_abort_during_po_refused[{label},resolve_first={rf}]",
                  r["abortRead"] == {"s": "po", "aborted": False, "atc": 201}
                  and _kinds(r) == want and r["out"]["po"]["status"] == 200
                  and r["out"]["po"]["fired"] is True and r["out"]["skip"] == "",
                  f"{r['abortRead']} {_kinds(r)} {r['out']}")
            check(f"fl1_abort_during_po_cleanup[{label},{rf}]", _clean_after(r), str(r))
    # A refused read during a po that 400s: the in-lane 3b PUT + re-shoot still run.
    r = _stage_node(on, key, n, _rules(po={"seq": [{"status": 400, "body": "", "hang": True},
                                                   {"status": 200, "body": _PO_ORDER}]}), abort=True)
    check("fl1_refused_read_keeps_3b_reshoot",
          r["abortRead"] == {"s": "po", "aborted": False, "atc": 201}
          and _kinds(r) == ["atc", "pre", "po", "put", "po"]
          and r["out"]["cvv"]["reshot"] is True and r["out"]["po"]["status"] == 200,
          f"{_kinds(r)} {r['out']['cvv']}")
    # The 3b re-shoot itself pending -> still stage 'po' -> refused.
    r = _stage_node(on, key, n, _rules(po={"seq": [{"status": 400, "body": ""},
                                                   {"status": 200, "body": _PO_ORDER, "hang": True}]}),
                    abort=True)
    check("fl1_pending_3b_refused", r["abortRead"] == {"s": "po", "aborted": False, "atc": 201}
          and _kinds(r) == ["atc", "pre", "po", "put", "po"] and r["out"]["po"]["status"] == 200,
          f"{r['abortRead']} {_kinds(r)}")

    # Every early exit deletes the entry (late read -> null -> terminal).
    early = (
        ("atc_401", _rules(atc={"status": 401, "body": "{}"}), ["atc"]),
        ("atc_throw", _rules(atc={"throw": "boom"}), ["atc"]),
        ("pre_424", _rules(pre={"status": 424, "body": ""}), ["atc", "pre"]),
        ("pre_throw", _rules(pre={"throw": "reset"}), ["atc", "pre"]),
        ("foreign", _rules(pre={"body": json.dumps({"cart_items": [{"tcin": TCIN}, {"tcin": "99999999"}]})}),
         ["atc", "pre"]),
        ("po_throw", _rules(po={"throw": "reset"}), ["atc", "pre", "po"]),
    )
    for label, rules, want in early:
        r = _stage_node(on, key, n, rules)
        check(f"fl1_early_exit_cleanup[{label}]", _kinds(r) == want and _clean_after(r),
              f"{_kinds(r)} {r.get('entriesAfter')} {r.get('lateRead')}")

    # Flag off: nothing is created in the page at all.
    off, _, _ = _render_chain(False)
    r = _stage_node(off, "", "", _rules())
    check("fl1_off_no_page_global", r["stageKeysAfter"] == [] and _kinds(r) == ["atc", "pre", "po"],
          str(r["stageKeysAfter"]))

    # Every flag combination: the tracked chain parses, places on the happy path
    # and never fires the place-order after an abort during pre_checkout.
    combos = []
    for qg in (None, "1"):
        for ts in (None, "1"):
            for bm in (None, "1"):
                for latched in (False, True):
                    combos.append((qg, ts, bm, latched))
    for qg, ts, bm, latched in combos:
        tag = f"qg={qg or 0},ts={ts or 0},bm={bm or 0},latched={int(latched)}"
        js, k, nn = _render_chain(True, cvv_required=latched, TARGET_FASTLANE_QTY_GUARD=qg,
                                  TARGET_FASTLANE_T_STAMPS=ts, TARGET_ATC_BYTEMATCH=bm)
        want = ["atc", "pre"] + (["put"] if latched else []) + ["po"]
        r = _stage_node(js, k, nn, _rules())
        check(f"fl1_combo_happy[{tag}]", _kinds(r) == want and r["out"]["po"]["status"] == 200
              and _clean_after(r), f"{_kinds(r)} {r['out']}")
        if ts:
            check(f"fl1_combo_t_stamps_kept[{tag}]", isinstance(r["out"]["atc"].get("t0"), (int, float)))
        if qg:
            check(f"fl1_combo_qty_kept[{tag}]", r["out"]["pre"].get("qty") == 2, r["out"]["pre"])
        r = _stage_node(js, k, nn, _rules(pre={"hang": True}), abort=True)
        check(f"fl1_combo_abort_pre_no_po[{tag}]", _kinds(r) == ["atc", "pre"]
              and r["out"]["po"]["fired"] is False and r["abortRead"]["aborted"] is True,
              f"{_kinds(r)} {r['abortRead']}")
        # Stripping the FL-1 fragments gives the same combo with FL-1 off.
        js_off, _, _ = _render_chain(False, cvv_required=latched, TARGET_FASTLANE_QTY_GUARD=qg,
                                     TARGET_FASTLANE_T_STAMPS=ts, TARGET_ATC_BYTEMATCH=bm)
        fr = pe_mod.render_fast_lane_stage_fragments(k, nn)
        s = js
        for name in ("open", "atc", "pre", "cvv", "po", "close"):
            s = s.replace(fr[name], "", 1)
        check(f"fl1_combo_only_adds_fragments[{tag}]", s == js_off)


class _HangThenReadTab:
    """First evaluate (the chain) hangs; later evaluates are the read-and-abort."""

    def __init__(self, ex, read=None, read_raises=None, read_hangs=False, chain_raises=None):
        self.ex = ex
        self.js = []
        self.read = read
        self.read_raises = read_raises
        self.read_hangs = read_hangs
        self.chain_raises = chain_raises
        self.inflight_at_read = []
        self.read_kwargs = []

    async def evaluate(self, js, await_promise=False, **kw):
        self.js.append(js)
        if len(self.js) == 1:
            if self.chain_raises:
                raise self.chain_raises
            await asyncio.sleep(30)
            return {}
        self.inflight_at_read.append(self.ex._po_inflight)
        self.read_kwargs.append(dict(kw, await_promise=await_promise))
        if self.read_hangs:
            await asyncio.sleep(30)
        if self.read_raises:
            raise self.read_raises
        return self.read


def _fl1_timeout(read=None, stage_track=True, extra_env=None, **tab_kw):
    """Run the real _api_fast_lane against a hanging chain with shortened
    wait_for budgets (12 s -> 60 ms, 2 s -> 10 ms). Returns (res, ex, tab, out)."""
    import io
    ex = _bare_executor()
    ex._po_inflight = False
    ex._po_ambiguous = False
    tab = _HangThenReadTab(ex, read=read, **tab_kw)
    real_wait_for = asyncio.wait_for

    async def quick_wait_for(aw, timeout):
        return await real_wait_for(aw, timeout * 0.005)

    buf = io.StringIO()
    asyncio.wait_for = quick_wait_for
    try:
        with _env(TARGET_FASTLANE_STAGE_TRACK="1" if stage_track else None, **(extra_env or {})), \
                contextlib.redirect_stdout(buf):
            res = _loop_run(ex._api_fast_lane(tab, TCIN, 2, "{}"))
    finally:
        asyncio.wait_for = real_wait_for
    return res, ex, tab, buf.getvalue()


_TODAY_TIMEOUT_KEYS = {"atc", "pre", "po", "skip", "elapsed"}


def _is_today_terminal(res):
    return (set(res) == _TODAY_TIMEOUT_KEYS and res["skip"] == "evaluate_timeout"
            and res["po"] == {"status": 0, "body": "", "fired": True}
            and res["atc"] == {"status": 0, "body": "", "cart_items": []}
            and res["pre"] == {"status": 0})


def test_fl1_python_timeout_branch():
    # Aborted at the ATC (qty guard on) -> evaluate_timeout_atc, not fired,
    # not terminal.
    QG = {"TARGET_FASTLANE_QTY_GUARD": "1"}
    res, ex, tab, out = _fl1_timeout({"s": "atc", "aborted": True, "atc": 0}, extra_env=QG)
    k = re.search(r"const _SK = '(__[0-9a-f]{12})'", tab.js[0]).group(1)
    nn = re.search(r"const _SN = '(f\d+)'", tab.js[0]).group(1)
    check("fl1_py_read_uses_chain_key_and_id",
          len(tab.js) == 2 and tab.js[1] == pe_mod.render_fast_lane_abort_js(k, nn), tab.js[1:])
    check("fl1_py_read_not_awaiting_promise", tab.read_kwargs == [{"await_promise": False}], tab.read_kwargs)
    check("fl1_py_inflight_true_during_read_false_after",
          tab.inflight_at_read == [True] and ex._po_inflight is False, tab.inflight_at_read)
    check("fl1_py_atc_aborted", res["skip"] == "evaluate_timeout_atc" and res["po"]["fired"] is False
          and res["atc"]["status"] == 0 and "elapsed" in res, str(res))
    check("fl1_py_atc_aborted_logged", "ABORTED before any place-order" in out
          and "skip=evaluate_timeout_atc" in out and "UNKNOWN" not in out, out)
    v, term = ex._apply_fast_lane_result(res, TCIN, 0.0)
    check("fl1_py_atc_aborted_not_terminal", v == "fallthrough" and term is None
          and not ex._po_ambiguous, v)
    # QG is forced on by WC-1 / WC-3 -> relabelled with either of them too.
    for flag in ("TARGET_WONCART_DIRECT", "TARGET_HELD_CART_REENTRY"):
        res, ex, tab, out = _fl1_timeout({"s": "atc", "aborted": True, "atc": 0}, extra_env={flag: "1"})
        check(f"fl1_py_atc_aborted_qg_forced_by[{flag}]", res["skip"] == "evaluate_timeout_atc"
              and res["po"]["fired"] is False, str(res))
    # Qty guard OFF: a landed orphan add plus a re-race could stack -> the
    # ATC-stage abort is NOT relabelled (today's terminal; the chain is still
    # aborted, so nothing more fires in the page).
    res, ex, tab, out = _fl1_timeout({"s": "atc", "aborted": True, "atc": 0})
    check("fl1_py_atc_aborted_qg_off_stays_terminal", _is_today_terminal(res) and len(tab.js) == 2
          and "NOT relabelled (qty guard off" in out and ex._po_inflight is False, f"{res} {out}")

    # Aborted at pre / cvv after a 2xx ATC -> synthesized pre_0 (won-cart eligible).
    for stage, atc in (("pre", 201), ("cvv", 201), ("pre", 200), ("cvv", 200)):
        res, ex, tab, out = _fl1_timeout({"s": stage, "aborted": True, "atc": atc})
        check(f"fl1_py_{stage}_aborted_pre0[{atc}]",
              res["skip"] == "pre_0" and res["atc"]["status"] == atc
              and res["po"]["fired"] is False and res["pre"]["status"] == 0
              and res.get("stage") == stage and ex._po_inflight is False, str(res))
        check(f"fl1_py_{stage}_aborted_eligible[{atc}]",
              pe_mod.woncart_eligible(res, 0, "") is True
              and ex._woncart_new_ledger(TCIN, 2, res, 1.0)["verified"] is False)
        v, term = ex._apply_fast_lane_result(res, TCIN, 0.0)
        check(f"fl1_py_{stage}_aborted_falls_through[{atc}]", v == "fallthrough" and not ex._po_ambiguous, v)

    # Everything else stays today's terminal dict (+ ambiguous via AC-1).
    others = (
        ("po_refused", {"s": "po", "aborted": False, "atc": 201}, {}),
        ("po_aborted_true_is_impossible_but_terminal", {"s": "po", "aborted": True, "atc": 201}, {}),
        ("pre_aborted_atc_0", {"s": "pre", "aborted": True, "atc": 0}, {}),
        ("pre_aborted_atc_424", {"s": "pre", "aborted": True, "atc": 424}, {}),
        ("unknown_stage", {"s": "init", "aborted": True, "atc": 201}, {}),
        ("aborted_truthy_not_true", {"s": "atc", "aborted": 1, "atc": 0}, {}),
        ("aborted_missing", {"s": "atc", "atc": 0}, {}),
        ("null", None, {}),
        ("non_dict", ("remote", "exception"), {}),
        ("read_raises", None, {"read_raises": RuntimeError("websocket closed")}),
        ("read_hangs", {"s": "atc", "aborted": True, "atc": 0}, {"read_hangs": True}),
    )
    for label, read, kw in others:
        res, ex, tab, out = _fl1_timeout(read, **kw)
        check(f"fl1_py_terminal[{label}]", _is_today_terminal(res) and len(tab.js) == 2
              and ex._po_inflight is False and "treating as no-response (non-retryable)" in out,
              f"{res} js={len(tab.js)}")
        with _env(TARGET_AMBIGUOUS_COMMIT_LATCH="1"):
            v, term = ex._apply_fast_lane_result(res, TCIN, 0.0)
        check(f"fl1_py_terminal_ambiguous[{label}]", v == "terminal"
              and term.get("ambiguous_commit") is True and ex._po_ambiguous is True
              and term["reason"] == "checkout_navigation_failed", str(term))

    # Flag off: no read evaluate, the dict is exactly today's.
    res, ex, tab, out = _fl1_timeout({"s": "atc", "aborted": True, "atc": 0}, stage_track=False)
    check("fl1_py_off_no_read", len(tab.js) == 1 and _is_today_terminal(res)
          and "stage read" not in out and "_SE." not in tab.js[0], f"{res} {len(tab.js)}")
    check("fl1_py_off_no_seq_bump", getattr(ex, "_fl_stage_seq", 0) == 0)

    # A non-timeout evaluate error keeps today's terminal path (no read).
    res, ex, tab, out = _fl1_timeout({"s": "atc", "aborted": True, "atc": 0},
                                     chain_raises=RuntimeError("Inspected target navigated or closed"))
    check("fl1_py_raise_path_unchanged", len(tab.js) == 1 and res["po"]["fired"] is True
          and res["skip"].startswith("evaluate_threw:"), str(res))

    # A purchase-timeout cancel during the read propagates and leaves
    # _po_inflight True (the hang branch then latches ambiguous).
    ex = _bare_executor()
    ex._po_inflight = False

    class _CancelReadTab(_HangThenReadTab):
        async def evaluate(self, js, await_promise=False, **kw):
            self.js.append(js)
            if len(self.js) == 1:
                await asyncio.sleep(30)
            raise asyncio.CancelledError()

    tab = _CancelReadTab(ex)
    real_wait_for = asyncio.wait_for

    async def quick_wait_for(aw, timeout):
        return await real_wait_for(aw, timeout * 0.005)

    cancelled = False
    asyncio.wait_for = quick_wait_for
    try:
        with _env(TARGET_FASTLANE_STAGE_TRACK="1"):
            try:
                _loop_run(ex._api_fast_lane(tab, TCIN, 2, "{}"))
            except asyncio.CancelledError:
                cancelled = True
    finally:
        asyncio.wait_for = real_wait_for
    check("fl1_py_cancel_during_read_propagates", cancelled and ex._po_inflight is True,
          f"cancelled={cancelled} inflight={ex._po_inflight}")

    # Normal completion with tracking on: result passes through, inflight clear.
    ex = _bare_executor()
    tab = _CapturingTab(result={"atc": {"status": 201}, "pre": {"status": 200},
                                "po": {"status": 200, "body": _PO_ORDER, "fired": True}, "skip": ""})
    with _env(TARGET_FASTLANE_STAGE_TRACK="1"):
        res = _loop_run(ex._api_fast_lane(tab, TCIN, 2, "{}"))
    check("fl1_py_normal_completion", res["po"]["status"] == 200 and ex._po_inflight is False
          and "elapsed" in res)


def test_fl1_outcome_table():
    f = pe_mod.fast_lane_timeout_outcome
    check("fl1_tbl_atc", (f({"s": "atc", "aborted": True, "atc": 0}) or {}).get("skip") == "evaluate_timeout_atc")
    check("fl1_tbl_atc_with_status_still_atc",
          (f({"s": "atc", "aborted": True, "atc": 201}) or {}).get("skip") == "evaluate_timeout_atc")
    check("fl1_tbl_pre_201", (f({"s": "pre", "aborted": True, "atc": 201}) or {}).get("skip") == "pre_0")
    check("fl1_tbl_cvv_200_float", ((f({"s": "cvv", "aborted": True, "atc": 200.0}) or {})
                                    .get("atc") or {}).get("status") == 200)
    for bad in (None, {}, [], "x", {"s": "po", "aborted": False},
                {"s": "pre", "aborted": True, "atc": True},
                {"s": "pre", "aborted": True, "atc": float("nan")},
                {"s": "pre", "aborted": True, "atc": float("inf")},
                {"s": "pre", "aborted": True, "atc": "201"},
                {"s": "pre", "aborted": True},
                {"s": "PRE", "aborted": True, "atc": 201},
                {"s": None, "aborted": True, "atc": 201}):
        check(f"fl1_tbl_terminal[{bad!r}]", f(bad) is None, f(bad))
    # Synthesized dicts are independent objects (callers mutate them).
    a = f({"s": "pre", "aborted": True, "atc": 201})
    a["atc"]["status"] = 0
    check("fl1_tbl_fresh_dicts", f({"s": "pre", "aborted": True, "atc": 201})["atc"]["status"] == 201)
    check("fl1_flag_parse", pe_mod.fastlane_stage_track_on() is False)
    with _env(TARGET_FASTLANE_STAGE_TRACK=" 1 "):
        on = pe_mod.fastlane_stage_track_on()
    with _env(TARGET_FASTLANE_STAGE_TRACK="true"):
        tr = pe_mod.fastlane_stage_track_on()
    check("fl1_flag_parse_strip_and_strict", on is True and tr is False)


def main():
    if not NODE:
        print("[SKIP] node not found — JS chain tests skipped")
    else:
        test_js_happy_path_places_order()
        test_js_bad_atc_never_reaches_place_order()
        test_js_unhydrated_cart_never_reaches_place_order()
        test_js_foreign_cart_item_never_reaches_place_order()
        test_js_place_order_throw_is_reported_as_fired()
        test_js_atc_throw_does_not_mark_fired()
        test_js_cvv_400_put_then_reshoot_wins()
        test_js_latched_account_pre_puts_before_po()
        test_js_cvv_put_failure_no_reshoot()
        test_js_po_429_does_not_trigger_cvv_put()

    test_success_marks_placed_and_parses_order_id()
    test_success_with_unparseable_body_still_succeeds()
    test_no_response_is_terminal_and_never_retryable()
    test_cvv_400_latches_and_falls_through()
    test_fast_selling_429_starts_cooldown()
    test_reservation_failure_falls_through_without_cooldown()
    test_chain_stopped_before_po_falls_through()
    test_evaluate_timeout_reports_fired()
    test_evaluate_raise_reports_fired()
    test_fast_selling_cooldown_can_be_disabled()
    test_fast_lane_cvv_source_and_validation()
    test_placed_via_cvv_reshoot_latches_for_pre_put()
    test_hold_cart_for_fast_selling()

    # Part 3: FL-1 (TARGET_FASTLANE_STAGE_TRACK). Node is REQUIRED for the
    # abort-protocol proof: without it the FL-1 tests fail rather than skip.
    fl1 = [test_fl1_js_structure_and_flag_off, test_fl1_python_timeout_branch, test_fl1_outcome_table]
    if NODE:
        fl1.insert(1, test_fl1_node_abort_protocol)
    else:
        FAILED.append("fl1_node_abort_protocol: node not found (FL-1 must not be armed unproven)")
    for fn in fl1:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    shutil.rmtree(_TMPDIR, ignore_errors=True)

    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()

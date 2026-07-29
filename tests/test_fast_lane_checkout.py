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

    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    if FAILED:
        for f in FAILED:
            print(f"  FAILED: {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()

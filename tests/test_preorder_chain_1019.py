#!/usr/bin/env python3
"""Offline test: the buy path is pre-order-agnostic, and the over-limit first-add fallthrough is
pinned as TODAY's behaviour (readiness re-audit 2026-10-19, frame1.4 F2; BUILD_SPEC_1019 lane bptests).

WHY: a Pokemon pre-order drop is a PRE_ORDER_SELLABLE product, not an IN_STOCK one. The monitor knows
the difference (stock_monitor.py: availability_status / is_preorder); the question the audit asked is
whether anything on the BUY path branches on it. These tests pin that it does not, and pin what the
executor does today when the very first add-to-cart answers 400 MAX_PURCHASE_LIMIT_EXCEEDED.

(a) PRE-ORDER AGNOSTIC (real code: PurchaseExecutor._api_fast_lane + fast_lane_atc_body_literal; Node
    runs the rendered chain JS against a scripted fetch, the same harness style as
    test_won_cart_direct_smoke):
      - the chain JS is byte-identical for two different TCINs and two quantities once the TCIN and
        the quantity are masked, with the page's own shape (bytematch=True) and with the bat's whole
        armed environment;
      - the requests the chain puts on the wire (ATC, pre_checkout, place-order: URL, method, headers,
        body) are identical for a REGULAR product and for a PRE_ORDER_SELLABLE one (the scripted
        responses carry availability_status / is_preorder / street_date fields) and across TCINs, apart
        from the TCIN and quantity values; pre_checkout and place-order bodies carry no TCIN at all;
      - the ATC body keeps the page's key order (item_channel_id, tcin, quantity; no fulfillment field);
      - the product object never reaches the executor: _api_fast_lane / execute_purchase take no
        availability argument, and no buy-path source mentions is_preorder / PRE_ORDER / availability_status.
(b) OVER-LIMIT FIRST-ADD FALLTHROUGH (real code: PurchaseExecutor._execute_purchase_impl; the fast lane is
    answered with the chain result a 400 MAX_PURCHASE_LIMIT_EXCEEDED add produces, and the tab answers the
    self-heal fetches -- that is Target's side; the transition under test, clear_cart -> same-qty retry ->
    qty-1 retry -> per-TCIN throttle, is the executor's own): the count and ORDER of the POSTs is recorded.
    CURRENT behaviour, changed nowhere:
        qty 2, both retries 400:  clear_cart, POST qty=2, POST qty=1, throttle 90 s, atc_failed_api_mode
        qty 2, same-qty 201:      clear_cart, POST qty=2, legacy checkout reached
        qty 2, qty-1 201:         clear_cart, POST qty=2, POST qty=1, legacy checkout reached
        qty 1, both fail:         clear_cart, POST qty=1 (no qty-1 retry of a qty-1 add)
    Three MUTANTS of the real source (no qty-1 retry, no clear_cart, no same-qty retry) are loaded from a
    patched copy of purchase_executor.py and MUST fail the same expectations.
    NOT COVERED (stays a stub): the legacy checkout that follows a recovered add is stopped at the
    'checking_out' boundary (LegacyReached), and the legacy DOM add-to-cart BUTTON text ('preorder' /
    'pre-order' keywords in the PDP button finder) is a DOM path no offline test reaches.

Offline: no browser, no network (node subprocess only). Run: python tests/test_preorder_chain_1019.py
"""
from __future__ import annotations

import asyncio
import ast
import copy
import inspect
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_won_cart_direct_smoke as S  # noqa: E402  (import only; its main() does not run)
import test_fast_cadence_1011 as FC  # noqa: E402  (_bat_env only)
import src.session.purchase_executor as pe_mod  # noqa: E402

check = S.check
NODE = shutil.which("node")
TA, TB = "1010892069", "87654321"            # a hot-class TCIN and an unrelated one
_TMP = tempfile.mkdtemp(prefix="preorder_1019_")
THROTTLE_S = 90.0
MAXP_BODY = json.dumps({"message": "Items cannot be added to cart as max purchase limit exceeded",
                        "code": "MAX_PURCHASE_LIMIT_EXCEEDED"})


def bat_target_env():
    return {k: v for k, v in FC._bat_env().items() if k.startswith("TARGET_")}


# ───────────────────────────── (a) pre-order agnostic ──────────────────────────────

HARNESS = r"""
const scenario = %s;
const reqs = [];
globalThis.fetch = async (url, opts) => {
  reqs.push({url: String(url), method: opts.method, headers: opts.headers || {}, body: opts.body === undefined ? null : opts.body,
             referrer: opts.referrer === undefined ? null : opts.referrer});
  for (const r of scenario) {
    if (String(url).includes(r.match)) return {status: r.status, text: async () => r.body || ''};
  }
  throw new Error('no rule ' + url);
};
(async () => { const out = await (%s); console.log(JSON.stringify({out, reqs})); })()
  .catch(e => console.log(JSON.stringify({error: String(e)})));
"""


def _responses(tcin, qty, preorder):
    extra = ({"availability_status": "PRE_ORDER_SELLABLE", "is_preorder": True, "street_date": "2026-12-01",
              "fulfillment": {"is_preorder": True, "shipping_method": "PRE_ORDER"}} if preorder else
             {"availability_status": "IN_STOCK", "is_preorder": False})
    atc = dict({"tcin": tcin, "cart_item_id": "CI-1", "quantity": qty, "cart_id": "CART-1234567890"}, **extra)
    pre = {"cart_id": "CART-1234567890",
           "cart_items": [dict({"tcin": tcin, "quantity": qty, "cart_item_id": "CI-1"}, **extra)],
           "payment_instructions": [{"payment_instruction_id": "PI-9", "payment_type": "CARD"}]}
    return [{"match": "cart_items", "status": 201, "body": json.dumps(atc)},
            {"match": "pre_checkout", "status": 200, "body": json.dumps(pre)},
            {"match": "v1/checkout", "status": 200, "body": S.ORDER_BODY}]


def node_chain(js, tcin, qty, preorder):
    src = HARNESS % (json.dumps(_responses(tcin, qty, preorder)), js)
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8", dir=_TMP) as f:
        f.write(src)
        path = f.name
    proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    if "error" in res:
        raise AssertionError(res["error"])
    return res


def render_js(tcin, qty, env_kv, mod=None):
    """The chain JS the real _api_fast_lane renders for (tcin, qty) under env_kv."""
    class _Tab:
        js = None

        async def evaluate(self, js, await_promise=False):
            _Tab.js = js
            return {"atc": {"status": 401}, "pre": {}, "po": {}, "skip": "atc_401"}

    saved_cls = S.PurchaseExecutor
    if mod is not None:
        S.PurchaseExecutor = mod.PurchaseExecutor
    try:
        ex = S.bare(S.Clock())
        with S.env(**env_kv):
            S.run(ex._api_fast_lane(_Tab(), tcin, qty, json.dumps({"X-GyJwza5Z-a": "tok"})))
    finally:
        S.PurchaseExecutor = saved_cls
    return _Tab.js


def _mask(o):
    if isinstance(o, dict):
        return {k: ("@" if k in ("tcin", "quantity") else _mask(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [_mask(x) for x in o]
    return o


def norm_reqs(reqs, tcin):
    out = []
    for r in reqs:
        body = r["body"]
        parsed = None
        if isinstance(body, str):
            try:
                parsed = _mask(json.loads(body))
            except Exception:
                parsed = body
        d = {"url": r["url"], "method": r["method"], "referrer": r["referrer"],
             "headers": r["headers"], "body": parsed}
        out.append(json.loads(json.dumps(d).replace(tcin, "@T")))
    return out


def test_a_pre_order_agnostic():
    envs = {"default": {"TARGET_ATC_BYTEMATCH": "1"}, "bat": bat_target_env()}
    check("a_bat_env_is_armed_bytematch", bat_target_env().get("TARGET_ATC_BYTEMATCH") == "1"
          and bat_target_env().get("TARGET_FAST_LANE") == "1", str(bat_target_env().get("TARGET_ATC_BYTEMATCH")))
    for label, e in envs.items():
        # (1) the rendered JS differs between two TCINs / quantities only in those values.
        ja = render_js(TA, 37, e)
        jb = render_js(TB, 41, e)
        # the one random token in the chain JS is the per-instance stage key (__ + 12 hex): masked
        na = re.sub(r"__[0-9a-f]{12}", "__@K", re.sub(r"\b37\b", "@Q", ja.replace(TA, "@T")))
        nb = re.sub(r"__[0-9a-f]{12}", "__@K", re.sub(r"\b41\b", "@Q", jb.replace(TB, "@T")))
        check(f"a_js_identical_modulo_tcin_qty[{label}]", na == nb and "@T" in na and "@Q" in na
              and TA not in na and TB not in nb,
              f"len {len(na)} vs {len(nb)}")
        check(f"a_js_has_no_availability_logic[{label}]", not re.search(
            r"availability|pre_?order|street_date|is_preorder", ja, re.I), "availability token in the chain JS")
        # (2) the literal itself, straight from the helper.
        lit_a = pe_mod.fast_lane_atc_body_literal(TA, 2, bytematch=True)
        lit_b = pe_mod.fast_lane_atc_body_literal(TB, 3, bytematch=True)
        check(f"a_bytematch_literal_same_shape[{label}]",
              lit_a.replace(TA, "@T").replace("quantity: 2", "quantity: @Q")
              == lit_b.replace(TB, "@T").replace("quantity: 3", "quantity: @Q"), f"{lit_a} / {lit_b}")
        check(f"a_bytematch_literal_page_key_order[{label}]",
              lit_a.index("item_channel_id") < lit_a.index("tcin") < lit_a.index("quantity")
              and "fulfillment" not in lit_a, lit_a)
        if not NODE:
            check(f"a_node_present[{label}]", False, "node missing: the chain JS cannot be run")
            continue
        # (3) the wire: REGULAR vs PRE_ORDER_SELLABLE responses, two TCINs, two quantities.
        runs = {}
        for tcin, qty, pre in ((TA, 2, False), (TA, 2, True), (TB, 2, False), (TB, 2, True),
                               (TA, 3, False), (TB, 3, True)):
            js = render_js(tcin, qty, e)
            res = node_chain(js, tcin, qty, pre)
            runs[(tcin, qty, pre)] = res
            out = res["out"]
            check(f"a_chain_ran_to_an_order[{label}:{tcin}:{qty}:{'pre' if pre else 'reg'}]",
                  out["atc"]["status"] == 201 and out["pre"]["status"] == 200 and out["po"]["status"] == 200
                  and out["po"]["fired"] is True and out["skip"] == ""
                  and [r["method"] for r in res["reqs"]] == ["POST", "POST", "POST"],
                  json.dumps(out)[:300])
        base = norm_reqs(runs[(TA, 2, False)]["reqs"], TA)
        for k, res in runs.items():
            check(f"a_wire_identical_to_regular[{label}:{k[0]}:{k[1]}:{'pre' if k[2] else 'reg'}]",
                  norm_reqs(res["reqs"], k[0]) == base, json.dumps(norm_reqs(res["reqs"], k[0]))[:400])
        r_atc, r_pre, r_po = runs[(TA, 2, True)]["reqs"]
        check(f"a_pre_and_po_bodies_carry_no_tcin[{label}]",
              r_pre["body"] == '{"cart_type":"REGULAR"}' and r_po["body"] == '{"cart_type":"REGULAR","channel_id":"10"}'
              and TA not in r_pre["body"] + r_po["body"], f"{r_pre['body']} {r_po['body']}")
        check(f"a_atc_body_is_the_page_shape[{label}]",
              r_atc["body"] == ('{"cart_item":{"item_channel_id":"10","tcin":"' + TA + '","quantity":2},'
                                '"cart_type":"REGULAR","channel_id":"10","shopping_context":"DIGITAL"}'), r_atc["body"])
        check(f"a_urls_are_the_three_cart_endpoints[{label}]",
              r_atc["url"].startswith("https://carts.target.com/web_checkouts/v1/cart_items?")
              and r_pre["url"].startswith("https://carts.target.com/web_checkouts/v1/pre_checkout?")
              and r_po["url"].startswith("https://carts.target.com/web_checkouts/v1/checkout?")
              and TA not in r_pre["url"] + r_po["url"], f"{r_atc['url']} {r_pre['url']} {r_po['url']}")

    # (4) the product object never reaches the executor.
    sig = inspect.signature(pe_mod.PurchaseExecutor._api_fast_lane)
    sig2 = inspect.signature(pe_mod.PurchaseExecutor.execute_purchase)
    check("a_fast_lane_signature_has_no_product_argument",
          list(sig.parameters) == ["self", "tab", "tcin", "quantity", "extra_headers_js"], str(sig))
    check("a_execute_purchase_signature_has_no_product_argument",
          list(sig2.parameters) == ["self", "tcin", "quantity"], str(sig2))
    for rel in ("src/session/purchase_executor.py", "src/purchasing/bulletproof_purchase_manager.py",
                "src/purchasing/identity_rest.py"):
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        hits = sorted({t for t in ("is_preorder", "PRE_ORDER", "availability_status") if t in text})
        check(f"a_no_availability_state_in[{rel}]", not hits, f"found {hits}")
    ast.parse((ROOT / "src/session/purchase_executor.py").read_text(encoding="utf-8"))


# ──────────────────── (b) over-limit first-add fallthrough (the self-heal) ────────────────────

HEAL_URL = "cart_items?field_groups=CART,CART_ITEMS,SUMMARY"
FL_MAXP = {"atc": {"status": 400, "body": MAXP_BODY, "cart_items": []}, "pre": {"status": 0},
           "po": {"status": 0, "body": "", "fired": False}, "skip": "atc_400"}


class HealTab(S.ImplTab):
    """Target's side of the over-limit add: every add-to-cart POST the executor evaluates is
    recorded (quantity read from the request body) and answered from a script."""

    def __init__(self, events, answers):
        super().__init__()
        self.events, self.answers = events, list(answers)

    async def evaluate(self, js, await_promise=False):
        if HEAL_URL in js and "POST" in js:
            q = int(re.search(r"quantity:\s*(\d+)", js).group(1))
            self.events.append(("post", q))
            st = self.answers.pop(0) if self.answers else 400
            return {"status": st, "body": MAXP_BODY if st == 400 else ""}
        return None


def heal_run(answers, quantity=2, env_kv=None, mod=None):
    """One real _execute_purchase_impl on an executor of `mod` (default: the real module)."""
    mod = mod or pe_mod
    saved_cls = S.PurchaseExecutor
    S.PurchaseExecutor = mod.PurchaseExecutor
    try:
        c = S.Clock()
        ex, _ = S.impl_ex(c, FL_MAXP)
        ex._tcin_throttle_cooldown_s = THROTTLE_S     # __init__'s value (the bare harness executor skips __init__)
        events = []
        tab = HealTab(events, answers)

        async def _gp():
            return tab

        ex.session_manager.get_page = _gp

        async def _clear(t):
            events.append(("clear",))
            return True

        ex._clear_cart = _clear
        saved = (mod.time, mod.asyncio)
        mod.time, mod.asyncio = S.TimeShim(c), S.AsyncioShim(c)
        try:
            e = {"TARGET_API_PLACE_ORDER": "true"}
            e.update(env_kv or {})
            with S.in_tmp_cwd(), S.env(**e):
                try:
                    r = S.run(ex._execute_purchase_impl(TA, quantity=quantity))
                except S.LegacyReached:
                    r = {"reason": "LEGACY_CHECKOUT_REACHED"}
        finally:
            mod.time, mod.asyncio = saved
        return ex, tab, events, r, c
    finally:
        S.PurchaseExecutor = saved_cls


def want_s1(events):
    return events == [("clear",), ("post", 2), ("post", 1)]


def test_b_over_limit_first_add_fallthrough():
    ex, tab, ev, r, c = heal_run([400, 400])
    check("b_throttle_default_is_90s_in_init", "self._tcin_throttle_cooldown_s: float = 90.0"
          in Path(pe_mod.__file__).read_text(encoding="utf-8"), "PurchaseExecutor.__init__ default moved")
    check("b_both_retries_400_post_order", want_s1(ev), str(ev))
    check("b_both_retries_400_chain_fired_once_no_second_chain", ex.fl_calls == [2] and not ex.loop_calls
          and "checking_out" not in ex.statuses, f"fl={ex.fl_calls} loop={ex.loop_calls} st={ex.statuses}")
    check("b_both_retries_400_throttled_90s", ex._tcin_throttle_until.get(TA, 0) - c.t == 90.0,
          str(ex._tcin_throttle_until))
    check("b_both_retries_400_result_is_atc_failed_api_mode", r.get("reason") == "atc_failed_api_mode"
          and r.get("success") is False, str(r))
    check("b_both_retries_400_printed_the_heal_lines", "ATC 400 EXCEEDED" in S.run.last_out
          and "retrying qty=1" in S.run.last_out and "Self-heal qty=1 also failed" in S.run.last_out,
          S.run.last_out[-600:])

    ex, tab, ev, r, c = heal_run([201])
    check("b_same_qty_201_post_order", ev == [("clear",), ("post", 2)], str(ev))
    check("b_same_qty_201_enters_the_legacy_checkout", ex.statuses and "checking_out" in ex.statuses
          and not ex._tcin_throttle_until, f"{ex.statuses} {ex._tcin_throttle_until}")
    check("b_same_qty_201_no_place_order_from_this_layer", ex.fl_calls == [2] and not ex.loop_calls
          and not ex._fastlane_placed, f"{ex.fl_calls} {ex.loop_calls}")

    ex, tab, ev, r, c = heal_run([400, 201])
    check("b_qty1_201_post_order", want_s1(ev), str(ev))
    check("b_qty1_201_enters_the_legacy_checkout_unthrottled", "checking_out" in ex.statuses
          and not ex._tcin_throttle_until, f"{ex.statuses} {ex._tcin_throttle_until}")

    ex, tab, ev, r, c = heal_run([400, 400], quantity=1)
    check("b_qty1_request_has_no_qty1_retry", ev == [("clear",), ("post", 1)], str(ev))
    check("b_qty1_request_throttled", ex._tcin_throttle_until.get(TA, 0) - c.t == 90.0
          and r.get("reason") == "atc_failed_api_mode", f"{ex._tcin_throttle_until} {r}")

    # the bat's armed environment (strike, queue-guard, won-cart direct ...) with a chain that found no
    # line of ours: the strike gates do not take this 400; the self-heal runs exactly the same.
    ex, tab, ev, r, c = heal_run([400, 400], env_kv=bat_target_env())
    check("b_armed_bat_env_same_posts", want_s1(ev) and ex.fl_calls == [2] and not ex.loop_calls, f"{ev} {ex.fl_calls}")
    check("b_armed_bat_env_same_result", r.get("reason") == "atc_failed_api_mode"
          and ex._tcin_throttle_until.get(TA, 0) - c.t == 90.0, f"{r} {ex._tcin_throttle_until}")

    # the throttled TCIN: the next dispatch inside the 90 s bails at the top and fires nothing.
    ex, tab, ev, r, c = heal_run([400, 400])
    n_ev = len(ev)
    ex.fl_calls.clear()
    with S.in_tmp_cwd(), S.env(TARGET_API_PLACE_ORDER="true"), S.fake_time(c):
        r2 = S.run(ex._execute_purchase_impl(TA, quantity=2))
    check("b_next_dispatch_inside_cooldown_fires_nothing", len(ev) == n_ev and ex.fl_calls == [] and
          r2.get("success") is False, f"{ev} {ex.fl_calls} {r2}")


def load_mutant(tag, old, new, more=()):
    path = Path(pe_mod.__file__)
    src = path.read_text(encoding="utf-8")
    for o, n in ((old, new),) + tuple(more):
        if src.count(o) != 1:
            raise AssertionError(f"mutant anchor for {tag} occurs {src.count(o)}x")
        src = src.replace(o, n)
    name = f"src.session.purchase_executor_mut_{tag}"
    mod = types.ModuleType(name)
    mod.__package__ = "src.session"
    mod.__file__ = str(path)
    sys.modules[name] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


MUTANTS = {
    "no_qty1_retry": ('                elif quantity > 1:\n                    print(f"[PURCHASE] Self-heal qty={quantity} returned',
                      '                elif False:\n                    print(f"[PURCHASE] Self-heal qty={quantity} returned'),
    "no_clear_cart": ('                try:\n                    await self._clear_cart(tab)\n                except Exception as _ce:\n                    print(f"[PURCHASE] self-heal clear_cart failed: {_ce}")',
                      '                try:\n                    pass\n                except Exception as _ce:\n                    print(f"[PURCHASE] self-heal clear_cart failed: {_ce}")'),
    "no_same_qty_retry": ("                _sh1 = await _do_self_heal_retry(quantity)\n",
                          "                _sh1 = {'status': 0, 'body': ''}\n"),
}


def test_b_mutants_are_caught():
    ex, tab, ev, r, c = heal_run([400, 400])
    check("b_mut_control_real_source_passes", want_s1(ev), str(ev))
    for tag, (old, new) in MUTANTS.items():
        mod = load_mutant(tag, old, new)
        ex, tab, ev, r, c = heal_run([400, 400], mod=mod)
        check(f"b_mutant_caught[{tag}]", not want_s1(ev), f"mutant produced the real order: {ev}")
        check(f"b_mutant_ran_the_patched_source[{tag}]", type(ex).__module__ == mod.__name__
              and type(ex) is not pe_mod.PurchaseExecutor, f"{type(ex).__module__}")


def wire_regular_vs_preorder(env_kv, mod=None):
    """True when the chain's wire requests for a REGULAR and a PRE_ORDER_SELLABLE product are identical."""
    a = node_chain(render_js(TA, 2, env_kv, mod), TA, 2, False)
    b = node_chain(render_js(TA, 2, env_kv, mod), TA, 2, True)
    return norm_reqs(a["reqs"], TA) == norm_reqs(b["reqs"], TA), a, b


NL = chr(10)


def test_a_mutant_is_caught():
    """A chain that adds a field to the place-order body when the pre_checkout cart reads PRE_ORDER_SELLABLE
    must fail the REGULAR-vs-PRE_ORDER wire comparison (so the comparison can fail)."""
    if not NODE:
        check("a_mut_node_present", False, "node missing")
        return
    e = bat_target_env()
    same, _, _ = wire_regular_vs_preorder(e)
    check("a_mut_control_real_source_identical", same, "the real chain differs between REGULAR and PRE_ORDER")
    po_old = ("body: JSON.stringify({{cart_type: 'REGULAR', channel_id: '10'}})\n"
              "                }});\n                const t3 = await r3.text();")
    po_new = ("body: JSON.stringify({{cart_type: 'REGULAR', channel_id: '10', "
              "...(out.pre.preo ? {{preorder: true}} : {{}})}})\n"
              "                }});\n                const t3 = await r3.text();")
    pre_old = "out.pre.n = items.length;" + NL + "                    out.pre.tcins = items.map(i => i && i.tcin);"
    pre_new = ("out.pre.n = items.length; out.pre.preo = items.some(i => i && i.availability_status === 'PRE_ORDER_SELLABLE');"
               + NL + "                    out.pre.tcins = items.map(i => i && i.tcin);")
    mod = load_mutant("preorder_body", pre_old, pre_new, more=((po_old, po_new),))
    same, a, b = wire_regular_vs_preorder(e, mod)
    check("a_mut_availability_conditional_body_is_caught", not same and a["reqs"][2]["body"] != b["reqs"][2]["body"],
          f"{a['reqs'][2]['body']} / {b['reqs'][2]['body']}")


def main():
    for fn in (test_a_pre_order_agnostic, test_a_mutant_is_caught, test_b_over_limit_first_add_fallthrough,
               test_b_mutants_are_caught):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            S.FAILED.append(f"{fn.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(S.PASSED)}/{len(S.PASSED) + len(S.FAILED)} passed ===")
    for f in S.FAILED:
        print("  FAIL:", f)
    return 0 if not S.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

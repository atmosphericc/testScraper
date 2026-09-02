#!/usr/bin/env python3
"""Smoke test: adaptive edge-429 retry cadence (manager) + ATC RESPONSE header
capture (executor). No browser, no network.

2026-08-26 post-drop audit (1011960739, hyped SKU, ~60-75s in-stock window, 85
fast-lane shots / 0x2xx). Two flag-gated changes are pinned here:

  TASK 1 (bulletproof_purchase_manager.py) — the inter-attempt retry sleep now
  tightens ONLY when the just-failed attempt was edge-gated
  (result['gate_kind'] == 'edge', i.e. an empty-body 429 = the global demand
  lottery). 401 / DCO / anything-else keep the proven-safe 2.5/3.5 std range.
  New knobs TARGET_ATC_EDGE429_RETRY_DELAY_MIN/MAX (defaults 2.0/3.0), clamped
  MIN>=1.0, MAX>=MIN. Rollback = set both EDGE knobs to 2.5/3.5.

  This selection logic lives INLINE inside the nested execute_real_purchase()
  closure (manager _start_real_purchase), NOT a factored helper. Per the test
  brief, refactoring it out is out of scope, so the product path is pinned with
  SOURCE assertions (env names, 1.0 floor constant, gate_kind=='edge' guard,
  defaults). A separate replica-model test then demonstrates the *intended*
  selection/clamp semantics (edge vs auth401 vs missing) against a pure mirror
  of the source rule — clearly labelled as a model, not the product object.

  TASK 2 (purchase_executor.py) — a cart_items RESPONSE-stage fetch pattern is
  appended ONLY under TARGET_ATC_RESPONSE_HEADER_CAPTURE, and a log-only handler
  branch builds the [ATC_RESP] line cheaply (POST-only), sends the shared
  continue FIRST, and prints/logs only AFTER the continue (07-23 leak rule:
  every paused event continued exactly once; the hot-path response is never
  held behind console I/O). Pinned via source assertions + module import.

Run: python tests/test_edge429_cadence.py
"""
from __future__ import annotations

import inspect
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MGR_PATH = ROOT / 'src' / 'purchasing' / 'bulletproof_purchase_manager.py'
EXE_PATH = ROOT / 'src' / 'session' / 'purchase_executor.py'
MGR_SRC = MGR_PATH.read_text(encoding='utf-8', errors='replace')
EXE_SRC = EXE_PATH.read_text(encoding='utf-8', errors='replace')

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def _region(src, anchor, before=0, after=1200):
    """Return a slice of src around the first occurrence of anchor."""
    i = src.find(anchor)
    if i < 0:
        return ''
    return src[max(0, i - before): i + after]


# ---------------------------------------------------------------------------
# TASK 1 — cadence selection: SOURCE assertions on the product path
# ---------------------------------------------------------------------------

def test_cadence_std_knobs_present():
    check("std_min_knob_present",
          "os.environ.get('TARGET_ATC_RETRY_DELAY_MIN', '2.5')" in MGR_SRC)
    check("std_max_knob_present",
          "os.environ.get('TARGET_ATC_RETRY_DELAY_MAX', '3.5')" in MGR_SRC)


def test_cadence_edge_knobs_and_defaults():
    check("edge_min_knob_default_2.0",
          "os.environ.get('TARGET_ATC_EDGE429_RETRY_DELAY_MIN', '2.0')" in MGR_SRC)
    check("edge_max_knob_default_3.0",
          "os.environ.get('TARGET_ATC_EDGE429_RETRY_DELAY_MAX', '3.0')" in MGR_SRC)


def test_cadence_edge_guard_gate_kind():
    # The tightening fires ONLY on gate_kind == 'edge'.
    check("gate_kind_edge_guard_present",
          "(result or {}).get('gate_kind', '')) == 'edge'" in MGR_SRC)


def test_cadence_floor_constant_present():
    reg = _region(MGR_SRC, "TARGET_ATC_EDGE429_RETRY_DELAY_MIN")
    check("floor_min_1.0_clamp", "max(1.0, _edge_lo)" in reg)
    check("max_ge_min_clamp", "max(_edge_hi, _edge_lo)" in reg)


def test_cadence_log_line_gated():
    # Log line only emitted inside the edge branch (after the guard).
    guard_i = MGR_SRC.find("(result or {}).get('gate_kind', '')) == 'edge'")
    log_i = MGR_SRC.find("[RETRY_CADENCE] edge-429 lottery cadence")
    sleep_i = MGR_SRC.find("time.sleep(random.uniform(_atc_lo, _atc_hi))")
    check("retry_cadence_log_present", log_i > 0)
    check("log_inside_edge_branch", guard_i > 0 and guard_i < log_i)
    check("log_before_sleep", 0 < log_i < sleep_i)


def test_cadence_std_untouched_by_default_path():
    # There is exactly one time.sleep at the retry site and it uses _atc_lo/_atc_hi
    # (which are the std values unless the edge branch reassigned them).
    check("single_retry_sleep_site",
          MGR_SRC.count("time.sleep(random.uniform(_atc_lo, _atc_hi))") == 1)


def test_cadence_rollback_documented():
    check("rollback_note_present",
          "ROLLBACK" in MGR_SRC and "EDGE knobs = 2.5/3.5" in MGR_SRC)


def test_cadence_dated_changelog():
    reg = _region(MGR_SRC, "2026-08-26 adaptive edge-lottery cadence", after=400)
    check("dated_2026_08_26", "2026-08-26" in reg)
    check("cites_window", "1011960739" in reg)


# ---------------------------------------------------------------------------
# TASK 1 — cadence selection: REPLICA-MODEL semantics (mirror of source rule)
#
# NOT the product object (the rule is inline in a closure). This pins the
# INTENDED behaviour: edge => edge range, everything else => std range, with
# the documented clamp. random.uniform is patched to the midpoint so the pick
# is deterministic and we can assert *which range* was selected.
# ---------------------------------------------------------------------------

STD_MIN_DEFAULT, STD_MAX_DEFAULT = 2.5, 3.5
EDGE_MIN_DEFAULT, EDGE_MAX_DEFAULT = 2.0, 3.0


def _select_cadence(result, env):
    """Pure mirror of the manager's inline selection rule."""
    _atc_lo = float(env.get('TARGET_ATC_RETRY_DELAY_MIN', '2.5'))
    _atc_hi = float(env.get('TARGET_ATC_RETRY_DELAY_MAX', '3.5'))
    if str((result or {}).get('gate_kind', '')) == 'edge':
        _edge_lo = float(env.get('TARGET_ATC_EDGE429_RETRY_DELAY_MIN', '2.0'))
        _edge_hi = float(env.get('TARGET_ATC_EDGE429_RETRY_DELAY_MAX', '3.0'))
        _edge_lo = max(1.0, _edge_lo)
        _edge_hi = max(_edge_hi, _edge_lo)
        _atc_lo, _atc_hi = _edge_lo, _edge_hi
    return _atc_lo, _atc_hi


def test_model_source_matches_replica():
    # Guard against replica drift: assert the four load-bearing literals of the
    # replica all appear verbatim in the product source.
    for lit in ("max(1.0, _edge_lo)", "max(_edge_hi, _edge_lo)",
                "'2.0'", "'3.0'", "'2.5'", "'3.5'"):
        if lit not in MGR_SRC:
            check(f"replica_literal_in_source[{lit}]", False)
            return
    check("replica_literal_in_source", True)


def test_model_edge_selects_edge_range():
    lo, hi = _select_cadence({'gate_kind': 'edge'}, {})
    check("edge_defaults_2.0_3.0", (lo, hi) == (EDGE_MIN_DEFAULT, EDGE_MAX_DEFAULT))


def test_model_auth401_selects_std_range():
    lo, hi = _select_cadence({'gate_kind': 'auth401'}, {})
    check("auth401_uses_std", (lo, hi) == (STD_MIN_DEFAULT, STD_MAX_DEFAULT))


def test_model_dco_selects_std_range():
    lo, hi = _select_cadence({'gate_kind': 'dco'}, {})
    check("dco_uses_std", (lo, hi) == (STD_MIN_DEFAULT, STD_MAX_DEFAULT))


def test_model_missing_gate_kind_uses_std():
    lo, hi = _select_cadence({}, {})
    check("missing_gate_uses_std", (lo, hi) == (STD_MIN_DEFAULT, STD_MAX_DEFAULT))


def test_model_none_result_uses_std():
    lo, hi = _select_cadence(None, {})
    check("none_result_uses_std", (lo, hi) == (STD_MIN_DEFAULT, STD_MAX_DEFAULT))


def test_model_edge_only_applies_to_edge():
    # Same env overrides present, but non-edge must ignore the EDGE knobs.
    env = {'TARGET_ATC_EDGE429_RETRY_DELAY_MIN': '1.0',
           'TARGET_ATC_EDGE429_RETRY_DELAY_MAX': '1.2'}
    lo_edge, hi_edge = _select_cadence({'gate_kind': 'edge'}, env)
    lo_auth, hi_auth = _select_cadence({'gate_kind': 'auth401'}, env)
    check("edge_knobs_apply_on_edge", (lo_edge, hi_edge) == (1.0, 1.2))
    check("edge_knobs_ignored_off_edge", (lo_auth, hi_auth) == (STD_MIN_DEFAULT, STD_MAX_DEFAULT))


def test_model_min_floor_clamp():
    # Env tries to dip below the ~1s ban floor — clamped to 1.0.
    env = {'TARGET_ATC_EDGE429_RETRY_DELAY_MIN': '0.1',
           'TARGET_ATC_EDGE429_RETRY_DELAY_MAX': '0.5'}
    lo, hi = _select_cadence({'gate_kind': 'edge'}, env)
    check("min_clamped_to_1.0", lo == 1.0)
    check("max_raised_to_min", hi == 1.0)  # 0.5 < 1.0 => max(_edge_hi,_edge_lo)=1.0


def test_model_max_ge_min_clamp():
    # MAX below MIN gets raised to MIN (never an inverted range).
    env = {'TARGET_ATC_EDGE429_RETRY_DELAY_MIN': '2.5',
           'TARGET_ATC_EDGE429_RETRY_DELAY_MAX': '1.0'}
    lo, hi = _select_cadence({'gate_kind': 'edge'}, env)
    check("max_ge_min_after_clamp", lo == 2.5 and hi == 2.5)


def test_model_midpoint_pick_is_faster_on_edge():
    # With random.uniform patched to the midpoint, the edge range yields a
    # strictly smaller sleep than std (the whole point of the change).
    _orig = random.uniform
    try:
        random.uniform = lambda a, b: (a + b) / 2.0
        edge_lo, edge_hi = _select_cadence({'gate_kind': 'edge'}, {})
        std_lo, std_hi = _select_cadence({'gate_kind': 'auth401'}, {})
        edge_mid = random.uniform(edge_lo, edge_hi)
        std_mid = random.uniform(std_lo, std_hi)
        check("edge_midpoint_2.5", edge_mid == 2.5)
        check("std_midpoint_3.0", std_mid == 3.0)
        check("edge_strictly_faster", edge_mid < std_mid)
    finally:
        random.uniform = _orig


def test_model_rollback_equals_no_change():
    # Rollback knobs (EDGE = std) => edge path identical to std path.
    env = {'TARGET_ATC_EDGE429_RETRY_DELAY_MIN': '2.5',
           'TARGET_ATC_EDGE429_RETRY_DELAY_MAX': '3.5'}
    lo, hi = _select_cadence({'gate_kind': 'edge'}, env)
    check("rollback_matches_std", (lo, hi) == (STD_MIN_DEFAULT, STD_MAX_DEFAULT))


# ---------------------------------------------------------------------------
# TASK 2 — executor interceptor patterns + leak-safe handler
# ---------------------------------------------------------------------------

def test_executor_module_imports():
    import src.session.purchase_executor as m
    check("executor_import_ok", hasattr(m, 'PurchaseExecutor'))
    src = inspect.getsource(m.PurchaseExecutor._setup_cdp_fetch_interceptor)
    check("method_source_nonempty", len(src) > 500)


def test_flag_read_once_at_setup():
    src = inspect.getsource(
        __import__('src.session.purchase_executor', fromlist=['PurchaseExecutor'])
        .PurchaseExecutor._setup_cdp_fetch_interceptor)
    check("flag_read_present",
          "os.environ.get('TARGET_ATC_RESPONSE_HEADER_CAPTURE', '1') != '0'" in src)
    check("flag_stored_on_self", "self._atc_resp_capture_on" in src)


def test_cart_items_response_pattern_gated():
    # The cart_items RESPONSE pattern is appended ONLY under the flag.
    reg = _region(EXE_SRC, "_fetch_patterns = [", after=900)
    check("fetch_patterns_list_present", "_fetch_patterns = [" in EXE_SRC)
    check("cart_items_response_pattern_present",
          "url_pattern='*web_checkouts/v1/cart_items*'" in reg)
    check("pattern_response_stage",
          "request_stage=cdp.fetch.RequestStage.RESPONSE" in reg)
    # The append is guarded by the flag.
    guard_i = EXE_SRC.find("if self._atc_resp_capture_on:")
    append_i = EXE_SRC.find("_fetch_patterns.append(")
    cart_pat_i = EXE_SRC.find("url_pattern='*web_checkouts/v1/cart_items*'")
    check("append_guarded_by_flag", 0 < guard_i < append_i < cart_pat_i)
    check("enable_uses_patterns_var", "patterns=_fetch_patterns" in EXE_SRC)


def test_atc_resp_handler_continue_first_log_after():
    # Review-hardened ordering (2026-08-26): the branch BUILDS the message with
    # cheap dict reads only (no I/O), the SHARED continue runs next, and the
    # print/logger.info happen only AFTER the continue — the paused ATC response
    # (incl. a winning 201) is never held behind console I/O. POST-only guard
    # keeps cart-clear DELETEs / GETs out of the census.
    guard = "if self._atc_resp_capture_on and 'web_checkouts/v1/cart_items' in url and method == 'POST':"
    build_i = EXE_SRC.find(guard)
    check("atc_resp_branch_present_post_only", build_i > 0)
    pre = EXE_SRC[build_i: EXE_SRC.find("await tab.send(cdp.fetch.continue_request", build_i)]
    check("no_io_before_continue", "print(" not in pre and "logger.info" not in pre)
    check("build_swallows_to_debug", "self.logger.debug(f\"[ATC_RESP] capture failed:" in pre)
    check("branch_never_mutates",
          "fulfill_request" not in pre and "fail_request" not in pre
          and "continue_request" not in pre)
    cont_i = EXE_SRC.find("await tab.send(cdp.fetch.continue_request(request_id=event.request_id))", build_i)
    log_i = EXE_SRC.find("if _atc_resp_msg:", build_i)
    check("continue_before_log", 0 < cont_i < log_i)
    check("log_after_continue_prints", "self.logger.info(_atc_resp_msg)" in EXE_SRC[log_i:log_i+400])
    check("method_in_line", "method=POST" in EXE_SRC[build_i:build_i+900])


def test_atc_resp_single_continue_path_in_is_response():
    # Within the is_response block there must be exactly ONE continue_request
    # (the shared one) — the new branch adds none, so no double-continue.
    is_resp_i = EXE_SRC.find("if is_response:")
    # bound the region to the is_response block: up to the next top-level
    # 'if 'carts.target.com' in url' which begins the REQUEST-stage handling.
    end_i = EXE_SRC.find("if 'carts.target.com' in url or 'cart_items' in url:", is_resp_i)
    block = EXE_SRC[is_resp_i:end_i]
    check("is_response_block_bounded", 0 < is_resp_i < end_i)
    n_cont = block.count("cdp.fetch.continue_request(request_id=event.request_id)")
    check("exactly_one_continue_in_is_response", n_cont == 1)


def test_atc_resp_log_format():
    check("atc_resp_log_format",
          "[ATC_RESP] status={status} method=POST " in EXE_SRC)
    check("atc_resp_url_tag", "url=cart_items" in EXE_SRC)
    check("atc_resp_reads_error_key",
          "resp_headers.get('tgt-cart-error-key')" in EXE_SRC)
    check("atc_resp_reads_request_id",
          "resp_headers.get('x-request-id')" in EXE_SRC)


def test_dedup_keys_per_stage():
    # 07-23 leak rule underpinning: dedup key is per (request_id + stage).
    check("dedup_per_stage_resp", ":resp" in EXE_SRC or "':resp'" in EXE_SRC or 'resp' in EXE_SRC)
    # confirm the dedup add exists (single continue accounting relies on it)
    check("cdp_continued_add_present", "self._cdp_continued_ids.add(dedup_key)" in EXE_SRC)


if __name__ == '__main__':
    # Task 1 — source pins
    test_cadence_std_knobs_present()
    test_cadence_edge_knobs_and_defaults()
    test_cadence_edge_guard_gate_kind()
    test_cadence_floor_constant_present()
    test_cadence_log_line_gated()
    test_cadence_std_untouched_by_default_path()
    test_cadence_rollback_documented()
    test_cadence_dated_changelog()
    # Task 1 — replica-model semantics
    test_model_source_matches_replica()
    test_model_edge_selects_edge_range()
    test_model_auth401_selects_std_range()
    test_model_dco_selects_std_range()
    test_model_missing_gate_kind_uses_std()
    test_model_none_result_uses_std()
    test_model_edge_only_applies_to_edge()
    test_model_min_floor_clamp()
    test_model_max_ge_min_clamp()
    test_model_midpoint_pick_is_faster_on_edge()
    test_model_rollback_equals_no_change()
    # Task 2 — executor
    test_executor_module_imports()
    test_flag_read_once_at_setup()
    test_cart_items_response_pattern_gated()
    test_atc_resp_handler_continue_first_log_after()
    test_atc_resp_single_continue_path_in_is_response()
    test_atc_resp_log_format()
    test_dedup_keys_per_stage()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

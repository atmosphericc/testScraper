#!/usr/bin/env python3
"""Smoke test: ATC gate-wall circuit breaker (2026-08-09).

08-06->07 restock forensics (run_20260806_234205.log): all 3 identities hit a
Shape Device ID+ wall at add-to-cart — ~2,960 adds, ZERO 2xx, 429 DCO_RATE_LIMITED
hardening to 401 _ERR_AUTH_DENIED over the night. Device ID+ is hardware-anchored
so all 3 identities on this one machine share the score, and (per
docs/RETAILERS/target.md) continuing to hammer ACCELERATES the block. The
retry-while-in-stock loop otherwise fires up to TARGET_RETRY_WHILE_IN_STOCK_MAX
adds/window into that wall.

PurchaseExecutor._note_atc_gate_outcome counts CONSECUTIVE gate-denials per TCIN
(this executor instance == one identity) and, past the streak limit, arms the
EXISTING per-TCIN throttle (_tcin_throttle_until) so subsequent adds bail fast at
the top of _execute_purchase_impl and the manager's retry loop breaks on the
already-handled 'tcin_throttled_cooldown' reason.

Pins:
  - arms exactly at the streak limit, not before
  - both gate reasons (atc_failed_api_mode, rate_limited_429) count
  - ANY success resets the streak (fail-safe: a winning night never trips)
  - non-gate outcomes (OOS, wedge, checkout-busy, our own throttle bail) are
    neutral — neither increment nor reset
  - a stale high streak is INERT without a live gate-denial (never arms while
    adds succeed)
  - self-limiting: after the cooldown expires, a single probe-denial re-arms
  - kill-switch (TARGET_ATC_GATE_BREAKER=0) fully disables it
  - never raises into the purchase path (malformed result dict)
  - source contract: __init__ wires the env knobs; execute_purchase calls the
    hook on the impl result

No browser, no network. Run: python tests/test_atc_gate_breaker.py
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

SRC = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")

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


def _breaker(limit=8, cooldown=120.0, on=True):
    """Build a PurchaseExecutor with ONLY the breaker attributes set (bypasses
    the heavy __init__), mirroring test_native_atc_fallback.py's pattern."""
    ex = object.__new__(PurchaseExecutor)
    ex._atc_gate_breaker_on = on
    ex._atc_gate_streak_limit = limit
    ex._atc_gate_cooldown_s = cooldown
    ex._tcin_denial_streak = {}
    ex._tcin_throttle_until = {}
    return ex


def _denial(reason="atc_failed_api_mode"):
    return {"success": False, "tcin": "T", "reason": reason}


def _armed(ex, tcin="T"):
    return ex._tcin_throttle_until.get(tcin, 0.0) > time.time()


def test_arms_exactly_at_limit():
    ex = _breaker(limit=8)
    for _ in range(7):
        ex._note_atc_gate_outcome("T", _denial())
    check("not_armed_at_7", not _armed(ex))
    check("streak_is_7", ex._tcin_denial_streak.get("T") == 7)
    ex._note_atc_gate_outcome("T", _denial())
    check("armed_at_8", _armed(ex))


def test_both_gate_reasons_count():
    ex = _breaker(limit=4)
    ex._note_atc_gate_outcome("T", _denial("atc_failed_api_mode"))
    ex._note_atc_gate_outcome("T", _denial("rate_limited_429"))
    ex._note_atc_gate_outcome("T", _denial("atc_failed_api_mode"))
    check("mixed_gate_reasons_streak_3", ex._tcin_denial_streak.get("T") == 3)
    check("not_armed_at_3", not _armed(ex))
    ex._note_atc_gate_outcome("T", _denial("rate_limited_429"))
    check("armed_at_4_mixed", _armed(ex))


def test_success_resets_streak():
    ex = _breaker(limit=8)
    for _ in range(7):
        ex._note_atc_gate_outcome("T", _denial())
    check("streak_7_before_success", ex._tcin_denial_streak.get("T") == 7)
    ex._note_atc_gate_outcome("T", {"success": True, "tcin": "T", "reason": "order_confirmed"})
    check("streak_reset_to_0", ex._tcin_denial_streak.get("T") == 0)
    check("not_armed_after_reset", not _armed(ex))
    # And it takes a full fresh run of the limit to arm again.
    for _ in range(7):
        ex._note_atc_gate_outcome("T", _denial())
    check("no_arm_7_after_reset", not _armed(ex))


def test_non_gate_reasons_neutral():
    for reason in ("cdp_wedged_pre_atc", "lock_timeout", "checkout_busy_retryable",
                   "cart_addition_failed", "button_not_found", "tcin_throttled_cooldown"):
        ex = _breaker(limit=3)
        ex._note_atc_gate_outcome("T", _denial())            # streak 1
        ex._note_atc_gate_outcome("T", _denial(reason))       # neutral
        ex._note_atc_gate_outcome("T", _denial(reason))       # neutral
        check(f"neutral_no_increment: {reason}", ex._tcin_denial_streak.get("T") == 1)
        check(f"neutral_no_arm: {reason}", not _armed(ex))


def test_stale_streak_inert_without_live_denial():
    # A high streak alone must NOT arm — arming requires a CURRENT gate-denial.
    # This is the property that makes the breaker unable to suppress a winning add.
    ex = _breaker(limit=3)
    ex._tcin_denial_streak["T"] = 99  # stale high streak from earlier
    ex._note_atc_gate_outcome("T", {"success": True, "tcin": "T", "reason": "order_confirmed"})
    check("success_with_stale_streak_resets_not_arms", ex._tcin_denial_streak.get("T") == 0 and not _armed(ex))


def test_self_limiting_after_cooldown_expiry():
    ex = _breaker(limit=3, cooldown=120.0)
    for _ in range(3):
        ex._note_atc_gate_outcome("T", _denial())
    check("armed_first_time", _armed(ex))
    # Simulate the cooldown having expired (the top-of-impl bail would now let a
    # single probe through). One probe-denial should immediately re-arm, since
    # the streak is already >= limit.
    ex._tcin_throttle_until["T"] = time.time() - 1.0
    check("throttle_expired", not _armed(ex))
    ex._note_atc_gate_outcome("T", _denial())
    check("rearms_on_single_probe_denial", _armed(ex))


def test_kill_switch_disables():
    ex = _breaker(limit=2, on=False)
    for _ in range(20):
        ex._note_atc_gate_outcome("T", _denial())
    check("kill_switch_never_arms", not _armed(ex))
    check("kill_switch_no_streak_state", ex._tcin_denial_streak.get("T", 0) == 0)


def test_per_tcin_isolation():
    ex = _breaker(limit=3)
    ex._note_atc_gate_outcome("A", _denial())
    ex._note_atc_gate_outcome("A", _denial())
    ex._note_atc_gate_outcome("B", _denial())
    check("tcin_A_streak_2", ex._tcin_denial_streak.get("A") == 2)
    check("tcin_B_streak_1", ex._tcin_denial_streak.get("B") == 1)
    check("neither_armed", not _armed(ex, "A") and not _armed(ex, "B"))


def test_never_raises_on_malformed():
    ex = _breaker(limit=2)
    for bad in ({}, {"success": None}, {"reason": None}, {"success": False}):
        try:
            ex._note_atc_gate_outcome("T", bad)
            check(f"no_raise: {bad}", True)
        except Exception as e:  # pragma: no cover
            check(f"no_raise: {bad}", False)
            print(f"    raised: {e}")


def _edge(reason="rate_limited_429", kind="edge"):
    return {"success": False, "tcin": "T", "reason": reason, "gate_kind": kind}


def test_edge_429_neutral_by_default():
    """2026-08-21: an EMPTY-body 429 (gate_kind='edge', the edge demand lottery)
    neither counts nor resets; only carts-service denials (401 / DCO-body 429)
    build the streak. 08-21 armed on 53/62 edge-429s and cut the only window."""
    ex = _breaker(limit=3)
    for _ in range(10):
        ex._note_atc_gate_outcome("T", _edge())
    check("edge_429_x10_never_arms", not _armed(ex) and ex._tcin_denial_streak.get("T", 0) == 0)
    # interleaved: 2 real denials, many edge, 1 real -> arms exactly on the 3rd REAL
    ex._note_atc_gate_outcome("T", _denial())
    ex._note_atc_gate_outcome("T", _edge())
    ex._note_atc_gate_outcome("T", _denial())
    for _ in range(5):
        ex._note_atc_gate_outcome("T", _edge())
    check("edge_does_not_reset_real_streak", ex._tcin_denial_streak.get("T") == 2 and not _armed(ex))
    ex._note_atc_gate_outcome("T", _edge("rate_limited_429", "dco"))
    check("dco_body_429_counts", ex._tcin_denial_streak.get("T") == 3 and _armed(ex))
    # legacy result dicts without gate_kind (the ladder's 401 path, older code) still count
    ex2 = _breaker(limit=2)
    ex2._note_atc_gate_outcome("T", _denial("rate_limited_429"))
    ex2._note_atc_gate_outcome("T", _denial("atc_failed_api_mode"))
    check("no_gate_kind_still_counts", _armed(ex2))


def test_edge_429_counts_when_opted_in():
    ex = _breaker(limit=3)
    ex._atc_gate_count_edge_429 = True  # TARGET_ATC_GATE_COUNT_EDGE_429=1
    for _ in range(3):
        ex._note_atc_gate_outcome("T", _edge())
    check("opt_in_restores_08_09_counting", _armed(ex))


def test_streak_ttl_decay():
    ex = _breaker(limit=3)
    ex._atc_gate_streak_ttl_s = 600.0
    ex._note_atc_gate_outcome("T", _denial())
    ex._note_atc_gate_outcome("T", _denial())
    check("two_recent_denials_streak_2", ex._tcin_denial_streak["T"] == 2)
    ex._tcin_denial_last_ts["T"] = time.time() - 601  # an earlier window
    ex._note_atc_gate_outcome("T", _denial())
    check("stale_streak_restarts_at_1_not_3", ex._tcin_denial_streak["T"] == 1 and not _armed(ex))
    ex._note_atc_gate_outcome("T", _denial())
    ex._note_atc_gate_outcome("T", _denial())
    check("fresh_streak_still_arms_at_limit", _armed(ex))
    # TTL 0 = never decays (the 08-09..08-20 behaviour)
    ex3 = _breaker(limit=2)
    ex3._atc_gate_streak_ttl_s = 0.0
    ex3._note_atc_gate_outcome("T", _denial())
    ex3._tcin_denial_last_ts["T"] = time.time() - 99999
    ex3._note_atc_gate_outcome("T", _denial())
    check("ttl_zero_never_decays", _armed(ex3))
    # attribute-less executor (older pickles / tests) must not raise
    ex4 = _breaker(limit=2)
    for a in ("_atc_gate_streak_ttl_s", "_tcin_denial_last_ts", "_atc_gate_count_edge_429"):
        if hasattr(ex4, a):
            delattr(ex4, a)
    try:
        ex4._note_atc_gate_outcome("T", _denial()); ex4._note_atc_gate_outcome("T", _denial())
        check("missing_new_attrs_safe", _armed(ex4))
    except Exception as e:  # pragma: no cover
        check("missing_new_attrs_safe", False); print(f"    raised: {e}")


def test_source_contract_2026_08_21():
    check("init_wires_count_edge_flag", "TARGET_ATC_GATE_COUNT_EDGE_429" in SRC and "_atc_gate_count_edge_429" in SRC)
    check("init_wires_streak_ttl", "TARGET_ATC_GATE_STREAK_TTL_S" in SRC and "_tcin_denial_last_ts" in SRC)
    check("count_edge_default_off", "os.environ.get('TARGET_ATC_GATE_COUNT_EDGE_429', '0') == '1'" in SRC)
    # the 429 bail tags the result so the breaker can tell edge from DCO
    check("429_bail_carries_gate_kind",
          re.search(r"'reason': 'rate_limited_429',\s*'gate_kind': 'dco' if \(atc_body or ''\)\.strip\(\) else 'edge'", SRC) is not None)
    # ATC-401 ladder short-circuit sits BEFORE the legacy ladder branch and is flag-gated
    m = re.search(r"elif atc_status == 401 and not getattr\(self, '_atc_401_ladder_on', False\):[\s\S]{0,1500}?"
                  r"'reason': 'atc_failed_api_mode',\s*'gate_kind': 'auth401'[\s\S]{0,400}?elif atc_status == 401:", SRC)
    check("401_ladder_short_circuit_precedes_ladder", m is not None)
    check("401_ladder_flag_default_off", "os.environ.get('TARGET_ATC_401_LADDER', '0') == '1'" in SRC)
    check("401_short_circuit_keeps_cart_hold_read",
          re.search(r"ladder OFF[\s\S]{0,600}?await self\._check_cart_hold\(tab, tcin, atc_status\)", SRC) is not None)
    # identity tag on the ATC result lines (per-account audit)
    check("ident_tag_on_chain_done", re.search(r"\[FAST_LANE\] chain done in[\s\S]{0,400}?ident=\{self\._ident_tag\(\)\}", SRC) is not None)
    check("ident_tag_on_atc_status_lines", SRC.count("ident={self._ident_tag()}") >= 4)
    # manager: inter-retry re-warm no longer forces the /cart reload by default
    MSRC = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("manager_rewarm_flag_default_off", "os.environ.get('TARGET_RETRY_FORCE_REWARM', '0') == '1'" in MSRC)
    check("manager_rewarm_uses_flag", MSRC.count("warm_shape_headers(force_fresh=_rewarm_force)") == 2
          and "warm_shape_headers(force_fresh=True)" not in MSRC)
    # 2026-08-22: dead-token mid-window repair (07-07 safety net for the ladder-OFF path)
    check("dead_token_flag_default_on",
          "os.environ.get('TARGET_ATC_DEAD_TOKEN_MIDWINDOW_REPAIR', '1') != '0'" in SRC
          and "_atc_dead_token_midwindow_repair" in SRC)
    # both warmup-heartbeat repair guards consult the flag (so a CONFIRMED dead
    # write-auth can repair mid-window, still 300s-throttled)
    check("dead_token_guard_relaxes_both_gates",
          SRC.count("getattr(self, '_atc_dead_token_midwindow_repair', True)") >= 2)
    # ...and it is still gated on the 300s throttle in both places
    check("dead_token_repair_still_throttled",
          SRC.count("self._last_bg_token_repair_ts > 300.0") >= 2)


def test_source_contract():
    check("init_wires_kill_switch",
          "TARGET_ATC_GATE_BREAKER" in SRC and "_atc_gate_breaker_on" in SRC)
    check("init_wires_streak_limit", "TARGET_ATC_GATE_STREAK_LIMIT" in SRC)
    check("init_wires_cooldown", "TARGET_ATC_GATE_COOLDOWN_S" in SRC)
    # execute_purchase must call the hook on the impl result before returning.
    m = re.search(
        r"_impl_result = await self\._execute_purchase_impl\([\s\S]{0,200}?"
        r"self\._note_atc_gate_outcome\(tcin, _impl_result\)[\s\S]{0,80}?return _impl_result",
        SRC)
    check("hook_wired_at_execute_purchase_chokepoint", m is not None)
    # Arming reuses the EXISTING throttle (no new bail reason invented).
    check("reuses_existing_throttle", "_tcin_throttle_until[tcin] = time.time() + self._atc_gate_cooldown_s" in SRC)


if __name__ == '__main__':
    test_arms_exactly_at_limit()
    test_both_gate_reasons_count()
    test_success_resets_streak()
    test_non_gate_reasons_neutral()
    test_stale_streak_inert_without_live_denial()
    test_self_limiting_after_cooldown_expiry()
    test_kill_switch_disables()
    test_per_tcin_isolation()
    test_never_raises_on_malformed()
    test_source_contract()
    test_edge_429_neutral_by_default()
    test_edge_429_counts_when_opted_in()
    test_streak_ttl_decay()
    test_source_contract_2026_08_21()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

#!/usr/bin/env python3
"""Smoke test: 2026-08-28 Fri 0-for Phase-2 fix stack (2026-08-31 audit).
No browser, no network.

The 08-28 night (run_20260827_224501.log): 14 in-stock windows, 2,680 fast-lane
shots, ZERO 2xx. Multi-agent audit + adversarial verification found the night
unwinnable at our Shape pass rate (100% of limiter-passing shots died 401) AND
~980 shots forfeited to the breaker plus ~1.0-1.15s/cycle of self-inflicted
per-retry work. Winning-night forensics: wave-first shots convert 32.6% vs 0.9%
for re-POSTs. Six flag-gated changes are pinned here:

  1. Breaker default OFF (purchase_executor.py) — premise refuted: 245 arms in
     9 runs, 0 preceded a 2xx; resting never lowered the hot-SKU 401.
  2. TARGET_RETRY_WARM=0 (bulletproof_purchase_manager.py) — skip the awaited
     per-retry dummy-POST warm; forced re-warm path preserved.
  3. TARGET_CART_HOLD_SKIP_EDGE (purchase_executor.py) — skip the hold GET on
     EMPTY-body 429s only (1,526 GETs / 0 hits / 21 runs); DCO/401 keep it.
  4. TARGET_SHOT_TTL_REFRESH (purchase_executor.py) — gate the shot-#1
     headers_age>60 proactive warm (both ATC + place-order sites).
  5. TARGET_ATC_DCO_AS_EDGE_CADENCE (manager) — ATC-level DCO 429 may use the
     edge cadence; guard keeps the pinned gate_kind=='edge' expression.
  6. TARGET_401_PULSE (manager) — after N consecutive carts-401s sleep 15-25s
     in place of ONE cadence sleep so the next shot re-enters wave-first;
     edge-429s neither count nor reset the streak (ticket doctrine unchanged
     on edge-lottery windows).
  7. TARGET_ATC_REFERRER_PDP (executor) — fetch `referrer` INIT option carries
     the real PDP URL (headers-object Referer is a forbidden name, never on
     the wire).

Plus bat arming pins + the CRLF rule.

Run: python tests/test_0828_phase2_fixes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MGR_PATH = ROOT / 'src' / 'purchasing' / 'bulletproof_purchase_manager.py'
EXE_PATH = ROOT / 'src' / 'session' / 'purchase_executor.py'
BAT_PATH = ROOT / 'run_bot_with_nightly_restart.bat'
MGR_SRC = MGR_PATH.read_text(encoding='utf-8', errors='replace')
EXE_SRC = EXE_PATH.read_text(encoding='utf-8', errors='replace')
BAT_SRC = BAT_PATH.read_text(encoding='utf-8', errors='replace')

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


def _region(src, anchor, before=0, after=1500):
    i = src.find(anchor)
    if i < 0:
        return ''
    return src[max(0, i - before): i + after]


# ---------------------------------------------------------------------------
# 1. Breaker default OFF
# ---------------------------------------------------------------------------

def test_breaker_default_off():
    check("breaker_default_off_in_executor",
          "os.environ.get('TARGET_ATC_GATE_BREAKER', '0') == '1'" in EXE_SRC)
    check("breaker_old_default_gone",
          "os.environ.get('TARGET_ATC_GATE_BREAKER', '1') != '0'" not in EXE_SRC)
    reg = _region(EXE_SRC, "TARGET_ATC_GATE_BREAKER", before=2200, after=200)
    check("breaker_flip_dated_and_evidenced",
          "2026-08-31" in reg and "245 arms" in reg)


# ---------------------------------------------------------------------------
# 2. Retry-path warm gate (manager)
# ---------------------------------------------------------------------------

def test_retry_warm_flag_wiring():
    check("retry_warm_flag_default_on",
          "os.environ.get('TARGET_RETRY_WARM', '1') != '0'" in MGR_SRC)
    check("forced_rewarm_overrides_skip",
          "_retry_warm_on = _rewarm_force or os.environ.get('TARGET_RETRY_WARM', '1') != '0'" in MGR_SRC)
    # The warm call still exists and sits inside the gate.
    gate_i = MGR_SRC.find("if _retry_warm_on:")
    warm_i = MGR_SRC.find("warm_shape_headers(force_fresh=_rewarm_force)", gate_i)
    skip_i = MGR_SRC.find("[RETRY_WARM] skipped (TARGET_RETRY_WARM=0)")
    check("warm_call_inside_gate", 0 < gate_i < warm_i < skip_i)
    check("skip_is_logged", skip_i > 0)


def test_retry_warm_replica_semantics():
    def _warm_on(env):
        _rewarm_force = env.get('TARGET_RETRY_FORCE_REWARM', '0') == '1'
        return _rewarm_force or env.get('TARGET_RETRY_WARM', '1') != '0'
    check("default_env_warms", _warm_on({}) is True)  # no behaviour change
    check("bat_env_skips", _warm_on({'TARGET_RETRY_WARM': '0'}) is False)
    check("forced_rewarm_always_warms",
          _warm_on({'TARGET_RETRY_WARM': '0', 'TARGET_RETRY_FORCE_REWARM': '1'}) is True)


# ---------------------------------------------------------------------------
# 3. Cart-hold edge skip (executor)
# ---------------------------------------------------------------------------

def test_cart_hold_skip_edge_wiring():
    check("knob_default_off",
          "os.environ.get('TARGET_CART_HOLD_SKIP_EDGE', '0') == '1'" in EXE_SRC)
    check("knob_stored_on_self", "self._cart_hold_skip_edge" in EXE_SRC)
    # 429 branch: the skip keys on BODY EMPTINESS (same rule as gate_kind).
    reg = _region(EXE_SRC, "_edge_429 = not (atc_body or '').strip()", after=700)
    check("edge_skip_guard_present",
          "getattr(self, '_cart_hold_skip_edge', False) and _edge_429" in reg)
    check("hold_still_consulted_when_not_skipped",
          "_hold_landed = await self._check_cart_hold(tab, tcin, atc_status)" in reg)
    # 401 branch keeps its unconditional hold read (silent lands documented).
    i401 = EXE_SRC.find("ladder OFF (TARGET_ATC_401_LADDER=0)")
    check("401_branch_hold_unconditional",
          i401 > 0 and "if await self._check_cart_hold(tab, tcin, atc_status):"
          in EXE_SRC[i401:i401 + 800])


def test_cart_hold_skip_replica_semantics():
    def _reads_cart(flag_on, atc_body):
        _edge_429 = not (atc_body or '').strip()
        return not (flag_on and _edge_429)
    check("edge_429_skipped_when_armed", _reads_cart(True, '') is False)
    check("dco_body_429_still_read", _reads_cart(True, '{"error":"DCO_RATE_LIMITED"}') is True)
    check("default_flag_off_reads_everything", _reads_cart(False, '') is True)


# ---------------------------------------------------------------------------
# 4. Shot-#1 TTL refresh gate (executor, both sites)
# ---------------------------------------------------------------------------

def test_shot_ttl_refresh_gate():
    check("knob_default_on",
          "os.environ.get('TARGET_SHOT_TTL_REFRESH', '1') != '0'" in EXE_SRC)
    gated = ("getattr(self, '_shot_ttl_refresh_on', True) "
             "and self._cached_cart_headers and headers_age > 60")
    check("both_ttl_sites_gated", EXE_SRC.count(gated) == 2)
    check("no_ungated_ttl_site_left",
          EXE_SRC.count("if self._cached_cart_headers and headers_age > 60:") == 0)


# ---------------------------------------------------------------------------
# 5. DCO-as-edge cadence (manager)
# ---------------------------------------------------------------------------

def test_dco_as_edge_guard():
    check("dco_flag_default_off",
          "os.environ.get('TARGET_ATC_DCO_AS_EDGE_CADENCE', '0') == '1'" in MGR_SRC)
    # The 08-26 pinned edge expression must survive verbatim...
    check("edge_guard_expression_preserved",
          "(result or {}).get('gate_kind', '')) == 'edge'" in MGR_SRC)
    # ...and the DCO arm rides the same guard line.
    check("dco_arm_on_same_guard",
          "== 'edge' or (_dco_as_edge and _gk == 'dco')" in MGR_SRC)


def test_dco_as_edge_replica_semantics():
    def _uses_edge_range(result, env):
        _gk = str((result or {}).get('gate_kind', ''))
        _dco_as_edge = env.get('TARGET_ATC_DCO_AS_EDGE_CADENCE', '0') == '1'
        return _gk == 'edge' or (_dco_as_edge and _gk == 'dco')
    check("edge_always_edge_range", _uses_edge_range({'gate_kind': 'edge'}, {}) is True)
    check("dco_std_by_default", _uses_edge_range({'gate_kind': 'dco'}, {}) is False)
    check("dco_edge_when_armed",
          _uses_edge_range({'gate_kind': 'dco'}, {'TARGET_ATC_DCO_AS_EDGE_CADENCE': '1'}) is True)
    check("auth401_never_edge_range",
          _uses_edge_range({'gate_kind': 'auth401'}, {'TARGET_ATC_DCO_AS_EDGE_CADENCE': '1'}) is False)


# ---------------------------------------------------------------------------
# 6. Wave-first 401 pulse (manager)
# ---------------------------------------------------------------------------

def test_pulse_knobs_and_defaults():
    check("pulse_flag_default_off", "os.environ.get('TARGET_401_PULSE', '0') == '1'" in MGR_SRC)
    check("pulse_streak_default_3", "os.environ.get('TARGET_401_PULSE_STREAK', '3')" in MGR_SRC)
    check("pulse_sleep_min_default_15", "os.environ.get('TARGET_401_PULSE_SLEEP_MIN', '15')" in MGR_SRC)
    check("pulse_sleep_max_default_25", "os.environ.get('TARGET_401_PULSE_SLEEP_MAX', '25')" in MGR_SRC)
    check("pulse_max_ge_min_clamp", "_pulse_hi = max(_pulse_hi, _pulse_lo)" in MGR_SRC)


def test_pulse_counter_source_pins():
    # 2026-09-09: the wave-first-only block (census) sits between the counter and
    # the pulse, so the region is wider and the pulse is now an `elif` behind it.
    reg = _region(MGR_SRC, "if _gk == 'auth401':", after=5200)
    check("auth401_increments", "_consec_401 += 1" in reg)
    check("edge_is_neutral", "elif _gk != 'edge':" in reg)
    check("pulse_guarded_by_flag_and_streak",
          "_pulse_on and _consec_401 >= _pulse_streak_n:" in reg)
    check("pulse_capped_by_deadline",
          "max(0.0, _retry_deadline - time.time()) + 1.0" in reg)
    check("pulse_log_line", "[PULSE401]" in reg)
    check("pulse_resets_counter_before_sleep",
          reg.find("_consec_401 = 0", reg.find("[PULSE401]")) > 0)
    # Exactly one std cadence sleep site (08-26 pin) + the pulse alternative.
    check("single_std_sleep_site",
          MGR_SRC.count("time.sleep(random.uniform(_atc_lo, _atc_hi))") == 1)
    check("pulse_sleep_site_present", "time.sleep(_pulse_s)" in MGR_SRC)


def _pulse_step(gk, consec, pulse_on=True, streak_n=3):
    """Pure mirror of the manager's pulse bookkeeping for one retry step.
    Returns (new_consec, pulsed)."""
    if gk == 'auth401':
        consec += 1
    elif gk != 'edge':
        consec = 0
    if pulse_on and consec >= streak_n:
        return 0, True
    return consec, False


def test_pulse_replica_three_401s_trigger():
    c, p = _pulse_step('auth401', 0)
    check("401_no_pulse_at_1", (c, p) == (1, False))
    c, p = _pulse_step('auth401', c)
    check("401_no_pulse_at_2", (c, p) == (2, False))
    c, p = _pulse_step('auth401', c)
    check("401_pulse_at_3_and_reset", (c, p) == (0, True))


def test_pulse_replica_edge_neutral():
    # 401,401,edge,edge,401 -> streak reaches 3 (edges don't reset) -> pulse.
    c = 0
    for gk in ('auth401', 'auth401', 'edge', 'edge'):
        c, p = _pulse_step(gk, c)
        check(f"no_pulse_mid_sequence[{gk}]", p is False)
    c, p = _pulse_step('auth401', c)
    check("edge_interleaved_401s_still_pulse", (c, p) == (0, True))


def test_pulse_replica_dco_resets():
    c = 0
    for gk in ('auth401', 'auth401'):
        c, p = _pulse_step(gk, c)
    c, p = _pulse_step('dco', c)
    check("dco_resets_streak", (c, p) == (0, False))
    c, p = _pulse_step('auth401', c)
    check("post_dco_count_restarts", (c, p) == (1, False))


def test_pulse_replica_edge_lottery_never_pulses():
    # A pure edge-429 lottery window: the counter never moves.
    c = 0
    pulsed = False
    for _ in range(50):
        c, p = _pulse_step('edge', c)
        pulsed = pulsed or p
    check("edge_lottery_full_cadence_kept", (c, pulsed) == (0, False))


def test_pulse_replica_flag_off_never_pulses():
    c = 0
    pulsed = False
    for _ in range(10):
        c, p = _pulse_step('auth401', c, pulse_on=False)
        pulsed = pulsed or p
    check("default_off_no_behaviour_change", pulsed is False)


# ---------------------------------------------------------------------------
# 7. PDP referrer init option (executor fast lane)
# ---------------------------------------------------------------------------

def test_referrer_pdp_wiring():
    check("knob_default_off",
          "os.environ.get('TARGET_ATC_REFERRER_PDP', '0') == '1'" in EXE_SRC)
    reg = _region(EXE_SRC, "_ref_init = ''", after=700)
    check("ref_init_gated_on_flag", "if getattr(self, '_atc_referrer_pdp', False):" in reg)
    check("ref_init_carries_pdp_url", "referrer: 'https://www.target.com/p/-/A-{tcin}'" in reg)
    check("ref_init_policy_full_url", "no-referrer-when-downgrade" in reg)
    check("ref_init_injected_into_atc_fetch",
          "credentials: 'include', {_ref_init}" in EXE_SRC)


# ---------------------------------------------------------------------------
# 8. Bat arming + CRLF rule
# ---------------------------------------------------------------------------

def test_bat_arming_pins():
    for pin in ("set TARGET_ATC_GATE_BREAKER=0",
                "set TARGET_LEVEL_REARM_S=3",
                "set TARGET_RETRY_WARM=0",
                "set TARGET_CART_HOLD_SKIP_EDGE=1",
                "set TARGET_SHOT_TTL_REFRESH=0",
                "set TARGET_ATC_DCO_AS_EDGE_CADENCE=1",
                "set TARGET_401_PULSE=1",
                "set TARGET_401_PULSE_STREAK=3",
                "set TARGET_ATC_REFERRER_PDP=1"):
        check(f"bat_pin[{pin}]", pin in BAT_SRC)


def test_bat_is_crlf():
    data = BAT_PATH.read_bytes()
    crlf = data.count(b'\r\n')
    bare_lf = data.count(b'\n') - crlf
    check("bat_pure_crlf", crlf > 0 and bare_lf == 0)


# 2026-09-17 hot-sku 0916 stage S8: every newly armed flag, EXACT value. A
# substring pin would let 'set X=5,15' pass on 'set X=5,150', so each pin must
# be a whole bat line, appear once, carry no trailing whitespace, and be the
# LAST assignment of that variable (cmd: the last set wins).
HOT_0916 = ("1010892078,1010892076,1010892069,1010892067,1010892068,1010892065,"
            "1012422107,1011407490,1010892075,1010892071,1012055696,1011960739,1011209279")
BAT_0916_PINS = (
    ("TARGET_AMBIGUOUS_COMMIT_LATCH", "1"),
    ("TARGET_AMBIGUOUS_COMMIT_LATCH_S", "1800"),
    ("TARGET_AMBIGUOUS_COMMIT_LATCH_PERSIST", "1"),     # review round R1 (R1-AC1-1)
    ("TARGET_RACE_STATE_STARTED_AT_GUARD", "1"),
    ("TARGET_STOCK_PROBE", "1"),
    ("TARGET_STOCK_HYST_S", "20"),
    ("TARGET_WONCART_DIRECT", "1"),
    ("TARGET_WONCART_SCHEDULE_S", "5,15"),
    ("TARGET_WONCART_STEADY_GAP_S", "45"),
    ("TARGET_WONCART_OOS_TAIL_TICKETS", "1"),
    ("TARGET_WONCART_MAX_TICKETS", "14"),
    ("TARGET_WONCART_CALL_MAX_S", "120"),
    ("TARGET_WONCART_HEADROOM_S", "45"),
    ("TARGET_WONCART_YIELD_FLEET", "1"),
    ("TARGET_HELD_CART_REENTRY", "1"),
    ("TARGET_HELD_CART_TTL_S", "900"),
    ("TARGET_RESHOOT_FORCE_REWARM", "0"),
    ("TARGET_HOLD_QUIET_WARMUP", "1"),
    ("TARGET_WARMUP_CYCLE_SKIP_ON_STOCK", "1"),
    ("TARGET_FASTLANE_SKIP_DUP_PRE", "1"),
    ("TARGET_WON_CART_RIDE_CLEAN_EXIT", "1"),
    ("TARGET_FASTLANE_STAGE_TRACK", "1"),
    ("TARGET_EXPOSURE_LOG", "1"),
    ("TARGET_FASTLANE_T_STAMPS", "1"),
    ("TARGET_FS_TICKET_LOG", "1"),
    ("TARGET_IDENT_CENSUS", "1"),
    ("TARGET_FASTLANE_LOG_CART_QTY", "1"),
    ("TARGET_REDSKY_STORE_OPTIONS_LOG", "1"),
    ("TARGET_HARVEST_SKIP_DISABLES_REPLAY", "1"),
    ("TARGET_HARVEST_MISS_PROBE", "1"),
    ("TARGET_HARVEST_MISS_SHOTS_MAX", "10"),
    ("TARGET_HARVEST_PX_PARK_S", "300"),
    ("TARGET_HARVEST_MISS_RENAV_LIVE", "0"),
    ("TARGET_BANK_GATE_ADAPTIVE", "1"),
    ("TARGET_HARVEST_FLUSH_ON_RELAUNCH", "1"),
    ("TARGET_SENTINEL_LOG_SKIPS", "1"),
    ("TARGET_CHROME_MAX_AGE_OFFSETS", "business:-300"),
    ("TARGET_CHROME_RELAUNCH_DESYNC_S", "120"),          # review round R1 (R1-ARM-2)
    ("TARGET_IDENTITY_REST", "0"),
)
# Built but deliberately NOT armed (U1=(a)/(b) guards built in review round R1
# but U1=(c) is the choice; U2 keeps qty 2; live re-nav waits for probe data;
# quiet level 2 is an experiment).
BAT_0916_UNARMED = ("TARGET_HOME_SHARE_GUARD", "TARGET_BG_SLOW_ACCOUNTS", "TARGET_BG_SLOW_FACTOR",
                    "TARGET_QTY_PER_TCIN", "TARGET_BOOT_CART_AUDIT", "TARGET_FASTLANE_QTY_GUARD",
                    "TARGET_HARVEST_BADLOAD_BACKOFF_S", "TARGET_IDENTITY_REST_S",
                    "TARGET_IDENTITY_REST_K", "TARGET_IDENTITY_REST_M", "TARGET_IDENTITY_REST_NEVER",
                    "TARGET_IDENTITY_REST_PROXIED_ONLY", "TARGET_IDENTITY_REST_STAGGER",
                    "TARGET_HOME_SHARE_GUARD_GUEST", "TARGET_HOME_SHARE_GUARD_PROTECT",
                    "TARGET_HOME_SHARE_GUARD_P401", "TARGET_HOME_SHARE_GUARD_TTL_S",
                    "TARGET_AMBIGUOUS_COMMIT_LATCH_FILE")


def _bat_lines():
    return BAT_PATH.read_bytes().decode('utf-8', 'replace').split('\r\n')


def _bat_last_value(lines, name):
    """Last cmd assignment of `name` (bare `set N=v` or quoted `set "N=v"`)."""
    val = None
    for line in lines:
        s = line.strip()
        low = s.lower()
        if low.startswith(f'set "{name.lower()}='):
            val = s[len(f'set "{name}='):]
            val = val[:-1] if val.endswith('"') else val
        elif low.startswith(f'set {name.lower()}='):
            val = s[len(f'set {name}='):]
    return val


def test_bat_hot_sku_0916_pins():
    lines = _bat_lines()
    for name, value in BAT_0916_PINS:
        pin = f"set {name}={value}"
        check(f"bat_0916_exact[{pin}]", lines.count(pin) == 1)
        check(f"bat_0916_last[{name}]", _bat_last_value(lines, name) == value)
    park = f'set "TARGET_PARK_ACCOUNT_TCINS=alt-1:{HOT_0916}"'
    check("bat_0916_park_exact_quoted", lines.count(park) == 1)
    check("bat_0916_park_last", _bat_last_value(lines, "TARGET_PARK_ACCOUNT_TCINS") == f"alt-1:{HOT_0916}")
    check("bat_0916_park_no_pipes", not any('TARGET_PARK_ACCOUNT_TCINS' in l and '|' in l for l in lines))
    for name in BAT_0916_UNARMED:
        check(f"bat_0916_unarmed[{name}]", _bat_last_value(lines, name) is None)
    # Kept values (plan P13) and the PULSE-inert note beside the pulse pin.
    for pin in ("set TARGET_FAST_SELLING_COOLDOWN_S=45", "set TARGET_401_PULSE=1",
                "set TARGET_CHROME_MAX_AGE_S=2100", "set TARGET_WON_CART_RIDE=1",
                "set TARGET_WAVE_FIRST_ONLY=1"):
        check(f"bat_0916_kept[{pin}]", lines.count(pin) == 1)
    i = lines.index("set TARGET_401_PULSE=1")
    check("bat_0916_pulse_inert_rem",
          any("TARGET_401_PULSE is INERT under TARGET_WAVE_FIRST_ONLY=1" in l for l in lines[i - 6:i]))
    # The new block: REM-or-set lines only, no trailing whitespace, cmd-safe REMs.
    s = next(k for k, l in enumerate(lines) if l.startswith("REM 2026-09-17 HOT-SKU FIX ARMING"))
    e = next(k for k in range(s, len(lines)) if lines[k].startswith("REM U2 per-TCIN qty pin"))
    blk = lines[s - 1:e + 6]
    check("bat_0916_block_bounded", blk[0].startswith("REM =====") and blk[-1].startswith("REM ====="))
    check("bat_0916_block_rem_or_set", all(l.startswith(("REM", "set ")) for l in blk))
    check("bat_0916_block_no_trailing_ws", all(l == l.rstrip() for l in blk))
    check("bat_0916_block_ascii", all(all(32 <= ord(c) < 127 for c in l) for l in blk))
    check("bat_0916_rem_cmd_safe",
          all(not (set('%!|&<>^') & set(l)) for l in blk if l.startswith("REM")))
    check("bat_0916_block_before_launch",
          e < next(k for k, l in enumerate(lines) if l.strip() == '"%PYTHON%" app.py'))


# ---------------------------------------------------------------------------
# 9. Modules still import/parse
# ---------------------------------------------------------------------------

def test_executor_imports():
    import src.session.purchase_executor as m
    check("executor_import_ok", hasattr(m, 'PurchaseExecutor'))


def test_manager_parses():
    import ast
    try:
        ast.parse(MGR_SRC)
        check("manager_parses", True)
    except SyntaxError as e:
        print(f"    SyntaxError: {e}")
        check("manager_parses", False)


if __name__ == '__main__':
    test_breaker_default_off()
    test_retry_warm_flag_wiring()
    test_retry_warm_replica_semantics()
    test_cart_hold_skip_edge_wiring()
    test_cart_hold_skip_replica_semantics()
    test_shot_ttl_refresh_gate()
    test_dco_as_edge_guard()
    test_dco_as_edge_replica_semantics()
    test_pulse_knobs_and_defaults()
    test_pulse_counter_source_pins()
    test_pulse_replica_three_401s_trigger()
    test_pulse_replica_edge_neutral()
    test_pulse_replica_dco_resets()
    test_pulse_replica_edge_lottery_never_pulses()
    test_pulse_replica_flag_off_never_pulses()
    test_referrer_pdp_wiring()
    test_bat_arming_pins()
    test_bat_is_crlf()
    test_bat_hot_sku_0916_pins()
    test_executor_imports()
    test_manager_parses()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)

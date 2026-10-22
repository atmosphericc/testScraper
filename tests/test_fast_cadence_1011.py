#!/usr/bin/env python3
"""Offline tests: FX-1011-CADENCE (2026-10-11) -- per-account fast lottery cadence.

  TARGET_FAST_CADENCE_IDENTS=<acct>[,<acct>...] | all   unset / empty / 0 = OFF (exact prior behaviour)
  TARGET_FAST_CADENCE_MIN_S / _MAX_S                     default 3.0 / 4.5 s, floor 1.0 s

For the listed accounts an edge 429 (ERR_A2C_TCIN_RATE_LIMITED) or an ATC-level DCO 429
schedules the next shot MIN-MAX s later instead of the wave-first cold re-entry 55-70 s
later. Everything else -- the race budget, [RETRY_OOS], the live-racer guard, the
Shape-scoped 401 handling (wave-first / bank gate / pulse) and FX-1006-YIELD -- is unchanged.

These tests drive the REAL race loop (_start_real_purchase -> racer threads -> the
retry-while-in-stock loop) against stub workers, with the manager module's `time.sleep`
replaced by a recorder so a 55-70 s cold re-entry costs nothing. No browser, no network,
no bot. Run: python tests/test_fast_cadence_1011.py
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time as _real_time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402  (env, quiet, check, race_mgr, _Worker, _race)
import test_mass_restock_1006 as MR  # noqa: E402  (EDGE, RACE_ENV, FLAGS, MSKU, _with_checker, _st)
import src.purchasing.bulletproof_purchase_manager as bpm  # noqa: E402

check, env, quiet = H.check, H.env, H.quiet
EDGE, RACE_ENV, FLAGS, MSKU = MR.EDGE, MR.RACE_ENV, MR.FLAGS, MR.MSKU
A, B = MR.A, MR.B
A401 = {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401"}
NEW = ("TARGET_FAST_CADENCE_IDENTS", "TARGET_FAST_CADENCE_MIN_S", "TARGET_FAST_CADENCE_MAX_S")
for _k in NEW:
    os.environ.pop(_k, None)
OFF = {k: None for k in NEW}
WAVE = (55.0, 70.0)
FAST = (3.0, 4.5)


class _FakeTime:
    """The manager module's `time`: a recording no-op `sleep`; everything else real."""
    def __init__(self):
        self.rec = []
        self._lock = threading.Lock()

    def sleep(self, s):
        with self._lock:
            self.rec.append(float(s))

    def __getattr__(self, name):
        return getattr(_real_time, name)

    def big(self):
        return [s for s in self.rec if s >= 1.0]


class _fake_sleep:
    def __enter__(self):
        self.ft = _FakeTime()
        self.saved = bpm.time
        bpm.time = self.ft
        return self.ft

    def __exit__(self, *a):
        bpm.time = self.saved


def _fleet(script=None, alt_script=None):
    sc = list(script or [EDGE])
    return [H._Worker(1, "primary", sc), H._Worker(2, "business", sc),
            H._Worker(3, "alt-1", list(alt_script or sc))]


def _race(ws, extra, tcin=A, pending=None):
    """One real race on `tcin` with MAX=3 shots per account and a recorded sleep."""
    m = H.race_mgr(ws)
    statuses = {tcin: MR._st()}
    if pending:
        for t in pending:
            m._states[t] = {"status": "ready"}
            statuses[t] = MR._st()
        now = _real_time.time()
        m._fx1006_miss_pending = {t: {"first_miss_ts": now, "window_start": now - 1.0} for t in pending}
    MR._with_checker(m, statuses)
    with env(**{**FLAGS, **RACE_ENV, "TARGET_RETRY_WHILE_IN_STOCK_MAX": "3", **OFF, **extra}), _fake_sleep() as ft:
        ok, out = quiet(H._race, m, tcin)
    return ok, out, ft, m


def _in(band, xs):
    return sum(1 for x in xs if band[0] - 1e-9 <= x <= band[1] + 1e-9)


# --------------------------------------------------------------------------- pure helpers

def test_cfg_parsing():
    c = bpm._fast_cadence_cfg({})
    check("cfg_unset_off", c["on"] is False and c["idents"] == set(), c)
    check("cfg_defaults", (c["lo"], c["hi"]) == FAST, c)
    for raw in ("", " ", "0", ",", "; ,"):
        check(f"cfg_off_for_{raw!r}", bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": raw})["on"] is False, raw)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "Alt-1, primary;W2/business"})
    check("cfg_names_lower_split", c["on"] and c["idents"] == {"alt-1", "primary", "w2/business"}, c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "0.2",
                               "TARGET_FAST_CADENCE_MAX_S": "0.1"})
    check("cfg_floor_1s_and_hi_ge_lo", (c["lo"], c["hi"]) == (1.0, 1.0), c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "garbage",
                               "TARGET_FAST_CADENCE_MAX_S": "nan"})
    check("cfg_garbage_defaults", (c["lo"], c["hi"]) == FAST, c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "6",
                               "TARGET_FAST_CADENCE_MAX_S": "2"})
    check("cfg_hi_raised_to_lo", (c["lo"], c["hi"]) == (6.0, 6.0), c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MAX_S": "999"})
    check("cfg_cap_60s", c["hi"] == 60.0, c)


def test_applies():
    on = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    check("applies_acct_part", bpm._fast_cadence_applies(on, "W3/alt-1") is True)
    check("applies_case", bpm._fast_cadence_applies(on, "W3/ALT-1") is True)
    check("applies_other_acct_no", bpm._fast_cadence_applies(on, "W1/primary") is False)
    check("applies_none_label_no", bpm._fast_cadence_applies(on, None) is False)
    check("applies_empty_label_no", bpm._fast_cadence_applies(on, "") is False)
    lbl = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "w2/business"})
    check("applies_full_label", bpm._fast_cadence_applies(lbl, "W2/business") is True)
    check("applies_full_label_other_slot_no", bpm._fast_cadence_applies(lbl, "W3/business") is False)
    for raw in ("all", "*", "all,alt-1"):
        c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": raw})
        check(f"applies_all_{raw!r}", all(bpm._fast_cadence_applies(c, l) for l in ("W1/primary", "W2/business", "x")), raw)
    off = bpm._fast_cadence_cfg({})
    check("applies_off_cfg_no", bpm._fast_cadence_applies(off, "W3/alt-1") is False)
    check("applies_bad_cfg_no", bpm._fast_cadence_applies(None, "W3/alt-1") is False
          and bpm._fast_cadence_applies({"on": True, "idents": None}, "W3/alt-1") is False)


# --------------------------------------------------------------------------- the real race loop

def test_off_state_wave_first_for_everyone():
    """Flag unset: every account takes the 55-70 s cold re-entry after each edge 429 (as armed
    since 09-25), nothing is printed about fast cadence, and each fires MAX=3 shots."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {})
    big = ft.big()
    check("off_race_finished", ok, out[-600:])
    check("off_each_fired_3", [w.purchase_executor.calls for w in ws] == [3, 3, 3],
          [w.purchase_executor.calls for w in ws])
    check("off_six_wave_sleeps", _in(WAVE, big) == 6 and len(big) == 6, big)
    check("off_no_fast_sleep", _in(FAST, big) == 0, big)
    check("off_no_fast_line", "[FAST_CADENCE]" not in out and "[RETRY_CADENCE]" not in out, out[-800:])
    check("off_wave_first_lines_all_three", out.count("cold re-entry in") == 6
          and all(f"ident=W{i}/{a}" in out for i, a in ((1, "primary"), (2, "business"), (3, "alt-1"))),
          out.count("cold re-entry in"))
    check("off_results_edge", len(m.recorded) == 3 and all(r.get("reason") == "rate_limited_429" for _, r in m.recorded),
          m.recorded)


def test_alt1_fast_others_wave_first():
    """IDENTS=alt-1: alt-1 re-shoots 3.0-4.5 s after each edge 429; primary and business keep
    the 55-70 s cold re-entry on the same race. The within-night A/B this flag exists for."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    big = ft.big()
    check("ab_race_finished", ok, out[-600:])
    check("ab_each_fired_3", [w.purchase_executor.calls for w in ws] == [3, 3, 3],
          [w.purchase_executor.calls for w in ws])
    check("ab_four_wave_two_fast", _in(WAVE, big) == 4 and _in(FAST, big) == 2 and len(big) == 6, big)
    check("ab_fast_line_alt1_once", out.count("[FAST_CADENCE] W3/alt-1 ") == 1 and out.count("[FAST_CADENCE]") == 1,
          [l for l in out.splitlines() if "FAST_CADENCE" in l])
    wave_lines = [l for l in out.splitlines() if "cold re-entry in" in l]
    check("ab_wave_lines_never_alt1", len(wave_lines) == 4 and not any("alt-1" in l for l in wave_lines), wave_lines)
    # the existing [RETRY_CADENCE] banner fires once per fast re-shot (alt-1's two); never for the controls
    check("ab_retry_cadence_banner_twice", out.count("[RETRY_CADENCE] edge-429 lottery cadence 3.0-4.5s") == 2
          and out.count("[RETRY_CADENCE]") == 2, [l for l in out.splitlines() if "RETRY_CADENCE" in l])
    check("ab_results_edge", len(m.recorded) == 3 and all(r.get("reason") == "rate_limited_429" for _, r in m.recorded),
          m.recorded)


def test_all_fast_custom_band():
    """IDENTS=all with a 2.0 s band: six 2.0 s waits, no cold re-entry anywhere."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "2",
                                "TARGET_FAST_CADENCE_MAX_S": "2"})
    big = ft.big()
    check("all_race_finished", ok, out[-600:])
    check("all_six_2s_sleeps", len(big) == 6 and all(abs(s - 2.0) < 1e-6 for s in big), big)
    check("all_no_wave_lines", "cold re-entry in" not in out, out[-800:])
    check("all_three_fast_lines", out.count("[FAST_CADENCE]") == 3, out.count("[FAST_CADENCE]"))


def test_fast_ident_401_stays_wave_first():
    """A Shape-scoped 401 on a fast account still takes the wave-first cold re-entry (and the
    bank gate / pulse paths are untouched): fast cadence is for the edge / DCO lottery only."""
    ws = _fleet(alt_script=[A401])
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    big = ft.big()
    check("f401_race_finished", ok, out[-600:])
    check("f401_six_wave_sleeps", _in(WAVE, big) == 6 and _in(FAST, big) == 0, big)
    wave_alt = [l for l in out.splitlines() if "cold re-entry in" in l and "alt-1" in l]
    check("f401_alt1_wave_first_lines", len(wave_alt) == 2 and all("ATC-level auth401" in l for l in wave_alt), wave_alt)


def test_fast_dco_is_fast_too():
    """An ATC-level DCO 429 (edge admitted, cart service throttled) on a fast account re-shoots at
    the fast band as well (DCO-as-edge cadence is armed; the DCO burst is off here)."""
    dco = {"success": False, "reason": "rate_limited_429", "gate_kind": "dco",
           "gate_body": "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"}
    ws = _fleet(alt_script=[dco])
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    big = ft.big()
    check("fdco_race_finished", ok, out[-600:])
    check("fdco_four_wave_two_fast", _in(WAVE, big) == 4 and _in(FAST, big) == 2 and len(big) == 6, big)


def test_fast_wait_still_yields():
    """FX-1006-YIELD inside the fast wait: with a skipped live TCIN pending, the fast account ends
    its window mid-wait (tagged [fast-cadence]) exactly as the wave-first accounts do."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {**MSKU, "TARGET_FAST_CADENCE_IDENTS": "alt-1", "TARGET_MULTI_SKU_MISS_REARM": "1",
                                "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}, pending=[B])
    check("fy_race_finished", ok, out[-600:])
    check("fy_three_yields", out.count("[WAVE_YIELD]") == 3, [l for l in out.splitlines() if "WAVE_YIELD" in l])
    check("fy_alt1_yield_tagged_fast", any("[WAVE_YIELD] W3/alt-1" in l and "[fast-cadence]" in l for l in out.splitlines()),
          [l for l in out.splitlines() if "WAVE_YIELD" in l])
    check("fy_each_fired_once", [w.purchase_executor.calls for w in ws] == [1, 1, 1],
          [w.purchase_executor.calls for w in ws])
    check("fy_reservations_released", m._worker_reservations == {}, m._worker_reservations)


def _bat_env():
    """Every plain `set NAME=value` in the production wrapper (C-1014-16: no test ran the
    ARMED combination -- DCO burst on, MAX 40, the 110 s budget -- before 2026-10-14)."""
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    out = {}
    for ln in bat.splitlines():
        mm = re.match(r"^set ([A-Z0-9_]+)=(.*)$", ln.strip())
        if mm and "%" not in mm.group(2) and "!" not in mm.group(2):
            out[mm.group(1)] = mm.group(2)
    return out


class _VirtTime:
    """Per-THREAD virtual clock for the manager module: sleep(s) advances only the calling
    racer's clock, so the armed 0.5 s-slice yield-aware wait and the 110 s budget behave as
    they would live while costing no real time."""
    def __init__(self):
        self._loc = threading.local()
        self.rec = []
        self._lock = threading.Lock()

    def _off(self):
        return getattr(self._loc, "off", 0.0)

    def time(self):
        return _real_time.time() + self._off()

    def sleep(self, s):
        self._loc.off = self._off() + max(0.0, float(s))
        with self._lock:
            self.rec.append(float(s))

    def __getattr__(self, name):
        return getattr(_real_time, name)


def _armed_race(ws, tcin=A):
    """One real race with the wrapper's own values, on per-thread virtual time; returns the
    virtual time of every shot per account."""
    m = H.race_mgr(ws)
    MR._with_checker(m, {tcin: MR._st()})
    benv = {k: v for k, v in _bat_env().items() if k.startswith("TARGET_")}
    # Single-TCIN race harness; the stub stock status is stamped once in real time, so
    # [RETRY_OOS] (armed, tested in test_retry_oos_stop) would read it stale on virtual time.
    benv.update(TARGET_MULTI_SKU_DISPATCH=None, TARGET_RETRY_STOP_WHEN_OOS="0")
    # 2026-10-22: the wrapper carries TARGET_FAST_CADENCE_IDENTS=0 (fast cadence reverted, C-1021-06 / C-1022-01).
    # These "armed" races test the fast-cadence code path under the wrapper's OTHER values, so force it on here;
    # the bat value itself is pinned by test_armed_manifest_1018.
    benv.update(TARGET_FAST_CADENCE_IDENTS="all")
    vt = _VirtTime()
    shots = {w.cfg.account_id: [] for w in ws}
    for w in ws:
        orig = w.purchase_executor.execute_purchase

        async def _stamped(tcin_, quantity=1, _o=orig, _a=w.cfg.account_id):
            shots[_a].append(bpm.time.time())
            return await _o(tcin_, quantity)
        w.purchase_executor.execute_purchase = _stamped
    saved = bpm.time
    bpm.time = vt
    try:
        with env(**{**FLAGS, **benv}):
            ok, out = quiet(H._race, m, tcin)
    finally:
        bpm.time = saved
    gaps = {a: [b - x for x, b in zip(ts, ts[1:])] for a, ts in shots.items()}
    return ok, out, shots, gaps, benv


def test_armed_combination_from_bat():
    """C-1014-16: the wrapper as armed (IDENTS=all, WAVE_FIRST_ONLY/EDGE=1, DCO burst on with
    REQUIRE_BANK=0, MAX=40, 110 s budget). Edge 429s re-shoot in the 3.0-4.5 s band up to MAX;
    an ATC-level FAST_SELLING first takes the <=2-shot burst (~1.0-1.5 s), then the fast band;
    a 401 keeps the 55-70 s cold re-entry."""
    benv = _bat_env()
    check("armed_bat_values", benv.get("TARGET_FAST_CADENCE_IDENTS") in ("all", "0")  # 10-22: reverted to 0 (C-1022-01); fast path forced in _armed_race
          and benv.get("TARGET_ATC_DCO_BURST") == "1" and benv.get("TARGET_RETRY_WHILE_IN_STOCK_MAX") == "40"
          and benv.get("TARGET_ATC_DCO_BURST_REQUIRE_BANK") == "0",
          {k: benv.get(k) for k in ("TARGET_FAST_CADENCE_IDENTS", "TARGET_ATC_DCO_BURST",
                                    "TARGET_RETRY_WHILE_IN_STOCK_MAX", "TARGET_ATC_DCO_BURST_REQUIRE_BANK")})
    mx = int(benv.get("TARGET_RETRY_WHILE_IN_STOCK_MAX", "40"))
    bud = float(benv.get("TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S", "110"))
    # Edge 429s only: each account re-shoots every 3.0-4.5 s until the 110 s budget (or MAX)
    # ends its window -- ~25-37 shots, never a cold re-entry.
    ws = _fleet()
    ok, out, shots, gaps, _ = _armed_race(ws)
    n = {a: len(v) for a, v in shots.items()}
    allg = [g for v in gaps.values() for g in v]
    check("armed_edge_race_finished", ok, out[-600:])
    check("armed_edge_many_shots_each", all(22 <= k <= mx for k in n.values()), n)
    check("armed_edge_all_gaps_fast", allg and all(FAST[0] - 0.01 <= g <= FAST[1] + 0.6 for g in allg),
          (min(allg or [0]), max(allg or [0])))
    check("armed_edge_window_within_budget", all(v[-1] - v[0] <= bud + FAST[1] + 1 for v in shots.values()),
          {a: round(v[-1] - v[0], 1) for a, v in shots.items()})
    check("armed_edge_no_wave_lines", "cold re-entry in" not in out, out[-500:])
    # ATC-level FAST_SELLING on alt-1: the <=2-shot DCO burst (short gaps, after the bounded
    # bank wait) then the fast band -- never a cold re-entry.
    dco = {"success": False, "reason": "rate_limited_429", "gate_kind": "dco",
           "gate_body": "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"}
    ws = _fleet(alt_script=[dco])
    ok, out, shots, gaps, _ = _armed_race(ws)
    ga = gaps["alt-1"]
    check("armed_dco_race_finished", ok, out[-600:])
    bursts = [l for l in out.splitlines() if "[DCO_BURST]" in l and "alt-1" in l]
    check("armed_dco_burst_logged", len(bursts) >= 1, [l[:160] for l in out.splitlines() if "DCO_BURST" in l][:6])
    check("armed_dco_first_two_short", len(ga) > 3 and all(g <= 8.0 for g in ga[:2]), ga[:4])
    check("armed_dco_rest_fast", all(FAST[0] - 0.01 <= g <= FAST[1] + 0.6 for g in ga[2:]),
          (min(ga[2:] or [0]), max(ga[2:] or [0])))
    check("armed_dco_no_wave", "cold re-entry in" not in out, out[-400:])
    # A Shape-scoped 401 on alt-1 keeps the 55-70 s cold re-entry; the others stay fast.
    ws = _fleet(alt_script=[A401])
    ok, out, shots, gaps, _ = _armed_race(ws)
    wave_alt = [l for l in out.splitlines() if "cold re-entry in" in l and "alt-1" in l]
    check("armed_401_wave_first", ok and len(wave_alt) >= 1 and all("auth401" in l for l in wave_alt), wave_alt[:3])
    check("armed_401_alt1_few_shots", 1 <= len(shots["alt-1"]) <= 3 and len(shots["primary"]) >= 22,
          {a: len(v) for a, v in shots.items()})


def test_source_wiring():
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("wiring_env_read_once", src.count("env.get('TARGET_FAST_CADENCE_IDENTS'") == 1)
    check("wiring_fast_on_used", src.count("_fast_on") == 4, src.count("_fast_on"))  # 1 definition + 3 uses
    check("wiring_kinds_override", "_wf_kinds = tuple(k for k in _wf_kinds if k == 'auth401')" in src)
    check("wiring_band_override", "_edge_lo, _edge_hi = float(_fast_cfg['lo']), float(_fast_cfg['hi'])" in src)
    check("wiring_yield_aware_sleep", "[fast-cadence]" in src
          and "elif _fast_on and _gk in ('edge', 'dco') and _wave_yield_on():" in src)
    check("wiring_single_std_sleep_site_kept", src.count("time.sleep(random.uniform(_atc_lo, _atc_hi))") == 1)
    suite = (ROOT / "tests" / "run_offline_suite.py").read_text(encoding="utf-8")
    check("wiring_listed_in_offline_suite", '"test_fast_cadence_1011"' in suite)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    n_set = len(re.findall(r"(?im)^\s*set\s+TARGET_FAST_CADENCE_IDENTS=", bat))
    check("wiring_bat_sets_idents_at_most_once", n_set <= 1, n_set)


def main():
    tests = [test_cfg_parsing, test_applies, test_off_state_wave_first_for_everyone,
             test_alt1_fast_others_wave_first, test_all_fast_custom_band, test_fast_ident_401_stays_wave_first,
             test_fast_dco_is_fast_too, test_fast_wait_still_yields, test_armed_combination_from_bat,
             test_source_wiring]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
